import json
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from openpyxl import load_workbook
from shapely.geometry import box

from leadengine.config import load_context
from leadengine.ledger import Ledger
from leadengine.stages import contacts, geo, score, validate

SCORING = load_context("Berlin").scoring


def districts():
    return gpd.GeoDataFrame({"district": ["West", "East"]},
                            geometry=[box(13.0, 52.4, 13.4, 52.6), box(13.4, 52.4, 13.8, 52.6)], crs=4326)


def located(rows):
    return pd.DataFrame(rows, columns=["company_id", "lat", "lng", "geo_source", "geo_precision"])


def test_point_gets_district():
    out = geo.assign_districts(located([["a", 52.5, 13.5, "osm", "exact"]]), districts())
    assert out.loc[0, "district"] == "East" and bool(out.loc[0, "in_city_boundary"]) is True


def test_point_outside_is_flagged():
    # Schönefeld (postcode 12529) passes the Berlin postcode range but lies outside the city.
    out = geo.assign_districts(located([["s", 52.38, 13.52, "osm", "exact"]]), districts())
    assert bool(out.loc[0, "in_city_boundary"]) is False and pd.isna(out.loc[0, "district"])


def test_missing_coordinates_are_kept():
    out = geo.assign_districts(located([["n", None, None, "none", "none"]]), districts())
    assert len(out) == 1 and out.loc[0, "in_city_boundary"] is None


def test_postcode_precision_gets_no_district():
    out = geo.assign_districts(located([["p", 52.5, 13.5, "nominatim", "postcode"]]), districts())
    assert pd.isna(out.loc[0, "district"])


def test_google_only_row_is_never_located_from_google():
    company = {"lat": None, "lng": None, "osm_id": None}
    out = geo.locate_company(company, None, geocoder=None)
    assert out["geo_source"] == "none"


class FakeNominatim:
    def __init__(self):
        self.calls = 0

    def get(self, *_, **__):
        self.calls += 1
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: [{"lat": "52.5", "lon": "13.4", "addresstype": "house"}])


def test_geocoder_rate_limits_and_caches(tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(raw_geo=tmp_path), ledger=Ledger(tmp_path / "l.json"),
                          pipeline={"nominatim": {"url": "u", "min_seconds_between_requests": 1.1},
                                    "http": {"user_agent": "Test/1"}})
    waits = []
    session = FakeNominatim()
    g = geo.Geocoder(ctx, session=session, sleep=waits.append, clock=lambda: 100.0)
    first = g.locate("Musterstr. 1", "10115", "Berlin")
    assert first["precision"] == "exact"
    g.locate("Musterstr. 1", "10115", "Berlin")
    g.locate("Musterstr. 2", "10115", "Berlin")
    assert session.calls == 2 and waits == [1.1]
    assert ctx.ledger.count("nominatim") == 2


def rules(**extra):
    base = {"website": "https://firma.de/", "managers": [], "team_people": [], "emails": []}
    return {**base, **extra}


def person(name):
    return {"name": name, "role": "Geschäftsführer", "source_url": "u", "confidence": "high"}


def email(address, generic=False):
    return {"email": address, "source_url": "u", "generic": generic}


def test_found_on_site():
    out = contacts.resolve_contact(rules(managers=[person("Erika Müller")], emails=[email("e.mueller@firma.de")]))
    assert (out["email_status"], out["contact_email"]) == ("found_on_site", "e.mueller@firma.de")


def test_pattern_inferred_from_another_named_person():
    out = contacts.resolve_contact(rules(managers=[person("Erika Müller"), person("Hans Beispiel")],
                                         emails=[email("hans.beispiel@firma.de")]))
    assert (out["email_status"], out["contact_email"]) == ("pattern_inferred", "erika.mueller@firma.de")
    assert out["contact_email_confidence"] == "low"


def test_generic_only():
    out = contacts.resolve_contact(rules(managers=[person("Erika Müller")], emails=[email("info@firma.de", True)]))
    assert (out["email_status"], out["contact_email"]) == ("generic_only", None)
    assert out["generic_email"] == "info@firma.de"


def test_none_status():
    assert contacts.resolve_contact(rules(managers=[person("Erika Müller")]))["email_status"] == "none"


def test_email_on_other_domain_is_ignored():
    out = contacts.resolve_contact(rules(managers=[person("Erika Müller")], emails=[email("e.mueller@fremd.de")]))
    assert out["email_status"] == "none"


def test_personal_shaped_mailbox_gives_an_inferred_pattern():
    out = contacts.resolve_contact(rules(managers=[person("Erika Müller")], emails=[email("k.schulz@firma.de")]))
    assert (out["contact_email"], out["email_status"]) == ("e.mueller@firma.de", "pattern_inferred")
    assert out["contact_email_confidence"] == "low"


@pytest.mark.parametrize("local,expected", [
    ("k.schulz", "f.last"), ("hans.beispiel", "first.last"),
    ("info.berlin", None), ("service.center", None), ("schulz", None), ("a.b.c", None),
])
def test_pattern_from_shape(local, expected):
    assert contacts.pattern_from_shape(local) == expected


def test_manager_preferred_over_team_lead():
    team = {**person("Lea Team"), "rank": 2, "role": "Leitung WEG"}
    out = contacts.resolve_contact(rules(managers=[person("Erika Müller")], team_people=[team]))
    assert out["contact_name"] == "Erika Müller"


def test_team_lead_used_when_no_manager():
    team = {**person("Lea Team"), "rank": 3, "role": "Heizkostenabrechnung"}
    assert contacts.resolve_contact(rules(team_people=[team]))["contact_name"] == "Lea Team"


def test_search_urls_need_a_name():
    assert contacts.search_urls(None, "X")["linkedin_search_url"] is None
    assert "Erika+M" in contacts.search_urls("Erika Müller", "X")["xing_search_url"]


def full_kpis(**extra):
    return {"units_managed": 3200, "management_types": ["WEG", "Miet"], "employees": 25,
            "remote_reading_signal": "mentioned", "metering_providers": ["Techem"], "digital_maturity": "high",
            "years_in_business": 20, "contact_name": "E M", "email_status": "found_on_site", "crawled": True,
            "hiring_signal": "mentioned", "berlin_locations": 2, **extra}


def test_full_data_scores_high():
    out = score.score_company(full_kpis(), SCORING)
    assert out["lead_tier"] == "A" and out["data_completeness"] == 100.0
    assert out["lead_score"] == 96


def test_self_managed_landlord_is_capped_at_tier_b():
    out = score.score_company(full_kpis(self_managed_landlord=True), SCORING)
    assert out["lead_tier"] == "B" and out["lead_score"] == 96
    assert "capped at Tier B" in out["score_reason"]


def test_cap_does_not_raise_a_lower_tier():
    cfg = {**SCORING, "tiers": {"A": 99, "B": 98}}
    out = score.score_company(full_kpis(self_managed_landlord=True), cfg)
    assert out["lead_tier"] == "C" and "capped" not in out["score_reason"]


def test_missing_data_is_neutral_not_zero():
    out = score.score_company({"crawled": False}, SCORING)
    assert out["lead_score"] == 50 and out["data_completeness"] == 0.0 and out["lead_tier"] == "B"


def test_not_mentioned_does_not_count_as_known():
    a = score.score_company({"crawled": False, "remote_reading_signal": "not_mentioned"}, SCORING)
    assert a["data_completeness"] == 0.0


@pytest.mark.parametrize("units,expected", [(50, 0.1), (499, 0.4), (500, 0.7), (9999, 1.0), (10000, 0.8)])
def test_unit_bins(units, expected):
    assert score.kpi_scores({"units_managed": units}, SCORING)["units_managed"][0] == expected


def test_commercial_only_scores_low():
    assert score.kpi_scores({"management_types": ["Gewerbe"]}, SCORING)["management_type"][0] == 0.2


def test_tier_boundaries(monkeypatch):
    cfg = {**SCORING}
    assert score.score_company({"crawled": True, "contact_name": None}, cfg)["lead_tier"] == "B"


def test_crawled_without_contact_is_penalised():
    with_contact = score.score_company({"crawled": True, "contact_name": "E M", "email_status": "found_on_site"}, SCORING)
    without = score.score_company({"crawled": True, "contact_name": None, "email_status": "none"}, SCORING)
    assert with_contact["lead_score"] > without["lead_score"]


def test_reason_uses_only_rule_labels():
    out = score.score_company(full_kpis(), SCORING)
    assert "3,200 units" in out["score_reason"]


def test_reason_says_when_data_is_limited():
    out = score.score_company({"crawled": False}, SCORING)
    assert "no KPI data" in out["score_reason"] and "limited data" in out["score_reason"]


def test_angle_names_provider():
    text = score.contact_angle({"metering_providers": ["Techem"], "remote_reading_signal": "not_mentioned"}, SCORING)
    assert "Techem" in text


def test_angle_has_default():
    assert "31.12.2026" in score.contact_angle({}, SCORING)


def test_duplicate_flag():
    df = pd.DataFrame({"domain": ["a.de", "a.de", None], "company_name": ["X", "Y", "Z"], "postcode": ["10115", "10115", None]})
    assert validate.add_duplicate_flags(df).tolist() == [True, True, False]


def lead(i):
    return {"company_id": f"hv_{i}", "website": "https://a.de", "company_name": "N", "company_name_source_url": "u",
            "units_managed": i, "management_types": ["WEG"]}


def test_sample_is_reproducible(tmp_path):
    df = pd.DataFrame([lead(i) for i in range(100)])
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    validate.write_sample(df, a, 42, 30)
    validate.write_sample(df, b, 42, 30)
    ids = lambda p: [c.value for c in load_workbook(p).active["A"]]
    assert ids(a) == ids(b) and len(ids(a)) == 31


def test_unfilled_sample_is_not_treated_as_answered(tmp_path):
    path = tmp_path / "s.xlsx"
    validate.write_sample(pd.DataFrame([lead(i) for i in range(3)]), path, 42, 3)
    assert validate.sample_has_answers(path) is False


def test_accuracy_excludes_not_verifiable(tmp_path):
    path = tmp_path / "s.xlsx"
    validate.write_sample(pd.DataFrame([lead(i) for i in range(4)]), path, 42, 4)
    wb = load_workbook(path)
    ws = wb.active
    col = next(c.column for c in ws[1] if c.value == "company_name_check")
    for row, answer in zip(range(2, 6), ["correct", "correct", "wrong", "not verifiable"]):
        ws.cell(row, col).value = answer
    wb.save(path)
    out = validate.accuracy(path)["company_name"]
    assert out["accuracy"] == 66.7 and out["not verifiable"] == 1
