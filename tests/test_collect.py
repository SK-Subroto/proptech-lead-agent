from types import SimpleNamespace

import pytest

from leadengine.config import load_context
from leadengine.ledger import Ledger
from leadengine.sources import google_places as gp
from leadengine.sources import osm
from leadengine.stages.collect import (
    classify, find_duplicate_groups, merge_group, non_target_reason, would_lose_google_rows,
)

PLATFORMS = ["facebook.com"]


def row(name, website=None, postcode=None, source="osm", **extra):
    return {"source": source, "match_name": name, "company_name": name if source == "osm" else None,
            "website": website, "postcode": postcode, **extra}


def group_sizes(rows):
    return sorted(len(g) for g in find_duplicate_groups(rows, PLATFORMS))


def test_same_domain_merges():
    assert group_sizes([row("A Verwaltung", "https://a.example"), row("Other Name", "http://www.a.example/x")]) == [2]


def test_platform_domain_does_not_merge():
    rows = [row("Erste Hausverwaltung", "https://facebook.com/a"), row("Zweite Verwaltungen", "https://facebook.com/b")]
    assert group_sizes(rows) == [1, 1]


def test_same_name_and_postcode_merges():
    assert group_sizes([row("Muster Hausverwaltung GmbH", postcode="10115"), row("Muster Hausverwaltung", postcode="10115")]) == [2]


def test_fuzzy_name_same_postcode_merges():
    assert group_sizes([row("Hausverwaltung Mustermann", postcode="10115"), row("Hausverwaltung Mustermanns", postcode="10115")]) == [2]


def test_same_name_different_postcode_stays_separate():
    assert group_sizes([row("Hausverwaltung Mueller", postcode="10115"), row("Hausverwaltung Mueller", postcode="13353")]) == [1, 1]


def test_similar_names_with_different_domains_stay_separate():
    rows = [row("Hausverwaltung Mustermann", "https://a.example", "10115"),
            row("Hausverwaltung Mustermanns", "https://b.example", "10115")]
    assert group_sizes(rows) == [1, 1]


def test_generic_name_never_matches_alone():
    assert group_sizes([row("Hausverwaltung", postcode="10115"), row("Hausverwaltung", postcode="10115")]) == [1, 1]


def test_merge_prefers_osm_values():
    rows = [row("Google Name", "https://a.example", source="google_discovery", lat=None, lng=None, google_place_id="P1"),
            row("Osm Name", "https://a.example", "10115", lat=52.5, lng=13.4, osm_id="node/1")]
    merged = merge_group(rows, PLATFORMS)
    assert merged["company_name"] == "Osm Name"
    assert (merged["lat"], merged["google_place_id"]) == (52.5, "P1")
    assert merged["sources"] == "google_discovery;osm"


def test_merge_never_takes_coordinates_from_google_only_rows():
    merged = merge_group([row("G", "https://a.example", source="google_discovery", lat=None, lng=None)], PLATFORMS)
    assert merged["lat"] is None and merged["company_name"] is None


def rules():
    return load_context("Berlin").keywords["non_target_rules"]


@pytest.mark.parametrize("name,reason", [
    ("Objektus GmbH", "competitor_metering_provider"),
    ("Techem Energy Services", "competitor_metering_provider"),
    ("Berzirksamt Pankow Immobilienverwaltung", "public_authority"),
    ("Hausmeisterservice Muster", "hausmeisterservice"),
    ("Immobilienmakler Muster", "estate_agent_only"),
    ("Muster Gebäudereinigung", "facility_management_only"),
    ("Hausmeister und Hausverwaltung Muster", None),
    ("Muster Hausverwaltung GmbH", None),
])
def test_non_target_rules(name, reason):
    assert non_target_reason(name, rules()) == reason


def test_classify_flags_postcode_outside_city():
    ctx = load_context("Berlin")
    assert classify({"match_name": "Muster Hausverwaltung", "postcode": "14467"}, ctx) == "outside_postcode_range"
    assert classify({"match_name": None, "postcode": None}, ctx) == "no_name"


def test_overpass_query_keeps_weg_case_sensitive():
    query = osm.build_query(load_context("Berlin"))
    weg_line = next(line for line in query.splitlines() if "WEG([^" in line)
    assert '",i]' not in weg_line and '["office"]' in weg_line


def test_osm_row_uses_center_for_ways():
    row_out = osm.to_row({"type": "way", "id": 5, "center": {"lat": 52.5, "lon": 13.4},
                          "tags": {"name": "X", "addr:street": "Musterstr.", "addr:housenumber": "1",
                                   "addr:postcode": "10115", "addr:city": "Berlin"}})
    assert (row_out["lat"], row_out["lng"]) == (52.5, 13.4)
    assert row_out["address"] == "Musterstr. 1, 10115 Berlin"


class FakeResp:
    status_code = 200

    def __init__(self, places, token=None):
        self._data = {"places": places}
        if token:
            self._data["nextPageToken"] = token

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, full=False):
        self.calls, self.full = 0, full

    def post(self, *_, **__):
        self.calls += 1
        if self.full:
            start = self.calls * 100
            return FakeResp([{"id": f"p{start + i}"} for i in range(20)], "next")
        return FakeResp([{"id": "p1", "websiteUri": "https://a.example", "businessStatus": "OPERATIONAL",
                          "addressComponents": [{"types": ["locality"], "longText": "Berlin"}]}])


def google_ctx(tmp_path, budget=900, limit=None):
    ctx = load_context("Berlin", limit)
    ctx.paths = SimpleNamespace(raw_google=tmp_path)
    ctx.ledger = Ledger(tmp_path / "ledger.json")
    ctx.google_api_key = "test-key"
    ctx.pipeline["google"]["monthly_request_budget"] = budget
    return ctx


def test_worst_case_matches_spec():
    assert gp.worst_case_requests(load_context("Berlin").city["google"]) == 4032


def test_missing_key_skips_cleanly(tmp_path):
    ctx = google_ctx(tmp_path)
    ctx.google_api_key = None
    assert gp.collect(ctx, session=None) == []


def test_full_run_is_locked_until_confirmed(tmp_path):
    ctx = google_ctx(tmp_path)
    ctx.pipeline["google"]["full_run_confirmed"] = False
    session = FakeSession()
    assert gp.collect(ctx, session=session, sleep=lambda s: None) == []
    assert session.calls == 0


def test_rerun_cannot_silently_drop_google_rows(tmp_path):
    path = tmp_path / "candidates.csv"
    path.write_text("company_id,sources\nhv_1,google_discovery\n", encoding="utf-8")
    assert would_lose_google_rows(path, [], "discovery_only", None) is True
    assert would_lose_google_rows(path, [{"x": 1}], "discovery_only", None) is False
    assert would_lose_google_rows(path, [], "off", None) is False


def test_mode_off_skips(tmp_path):
    ctx = google_ctx(tmp_path)
    ctx.pipeline["google"]["mode"] = "off"
    assert gp.collect(ctx, session=None) == []


def test_budget_guard_stops_and_keeps_results(tmp_path):
    ctx = google_ctx(tmp_path, budget=50)
    session = FakeSession(full=True)
    results = gp.discover(ctx, session=session, sleep=lambda s: None)
    assert session.calls == 50 and results
    assert ctx.ledger.count(gp.LEDGER_KEY) == 50


def test_dry_run_sends_one_request(tmp_path):
    ctx = google_ctx(tmp_path, limit=10)
    session = FakeSession()
    gp.discover(ctx, session=session, sleep=lambda s: None)
    assert session.calls == 1


def test_rerun_uses_stored_responses(tmp_path):
    ctx = google_ctx(tmp_path)
    gp.discover(ctx, session=FakeSession(), sleep=lambda s: None)
    second = FakeSession()
    gp.discover(ctx, session=second, sleep=lambda s: None)
    assert second.calls == 0


def test_discovery_row_publishes_only_id_and_website():
    out = gp.to_row({"id": "P", "websiteUri": "https://a.example", "displayName": {"text": "Name"}})
    assert out["company_name"] is None and out["address"] is None and out["lat"] is None
    assert out["google_place_id"] == "P"


def test_centre_first_starts_with_central_tile():
    ctx = load_context("Berlin")
    tiles = gp.centre_first(list(gp.grid(ctx.city["bbox"], 4)), ctx.city["center"])
    center_lat = ctx.city["center"]["lat"]
    middle = lambda t: abs((t["south"] + t["north"]) / 2 - center_lat)
    assert middle(tiles[0]) < middle(tiles[-1])
    assert len(tiles) == 16
