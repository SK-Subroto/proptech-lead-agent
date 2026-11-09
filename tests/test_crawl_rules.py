import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from leadengine.config import load_context
from leadengine.schemas import CompanyExtraction, missing_quotes
from leadengine.stages import crawl, rules

FIXTURES = Path(__file__).parent / "fixtures"
KEYWORDS = load_context("Berlin").keywords


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def text_of(name):
    return crawl.plain_text(BeautifulSoup(html(name), "lxml"))


def test_links_are_classified_and_external_ignored():
    links = crawl.discover_links(html("home.html"), "https://musterhaus-test.de/")
    assert links["impressum"] == "https://musterhaus-test.de/impressum"
    assert links["team"] == "https://musterhaus-test.de/team"
    assert links["leistungen"].endswith("/leistungen/weg-verwaltung")
    assert "datenschutz" not in " ".join(links.values())
    assert all("other.invalid" not in u for u in links.values())


def test_select_pages_limits_relevant_and_keeps_impressum():
    links = crawl.discover_links(html("home.html"), "https://musterhaus-test.de/")
    chosen = crawl.select_pages(links, 3)
    assert [k for k, _ in chosen] == ["impressum", "ueber_uns", "leistungen", "team"]


def test_cloudflare_email_is_decoded():
    soup = BeautifulSoup('<a data-cfemail="422f272e2e2730022f3b262d2f232b2c6c2627"></a>', "lxml")
    assert crawl.find_mailtos(soup) == ["meller@mydomain.de"]


def test_script_text_is_not_extracted():
    assert "script.invalid" not in text_of("impressum_gmbh.html")


def test_trim_respects_limit_prefers_relevant_and_drops_boilerplate():
    lines = ["Allgemeiner Fülltext ohne Bezug"] * 400
    pages = [{"kind": "home", "url": "https://a.test/", "text": "\n".join(lines + ["Wir betreuen 500 Wohnungen", "Haftung für Inhalte"])}]
    out = crawl.trim_pages(pages, 1000)
    assert len(out) <= 1000
    assert "500 Wohnungen" in out and "Haftung" not in out and "URL: https://a.test/" in out


def test_gmbh_impressum_fields():
    text = text_of("impressum_gmbh.html")
    assert rules.extract_company_name(text)["value"] == "Musterhaus Verwaltung GmbH"
    assert rules.extract_legal_form("Musterhaus Verwaltung GmbH") == "GmbH"
    assert rules.extract_register(text) == "HRB 123456 B"
    address = rules.extract_address(text)
    assert (address["postcode"], address["city"]) == ("10115", "Berlin")
    assert address["street"].startswith("Beispielstraße 12")


def test_gmbh_managers_found_with_title():
    names = [m["name"] for m in rules.extract_managers(text_of("impressum_gmbh.html"))]
    assert names == ["Erika Beispiel", "Dr. Hans-Peter von Testfeld"]


def test_fax_is_not_returned_as_phone():
    phones = rules.extract_phones(text_of("impressum_gmbh.html"))
    assert phones == ["+49 30 2222222"]


def test_sole_trader_impressum():
    text = text_of("impressum_einzel.html")
    assert [m["name"] for m in rules.extract_managers(text)] == ["Maria Testfeld"]
    assert rules.extract_company_name(text)["value"] == "Hausverwaltung Testfeld e.K."
    assert rules.extract_address(text)["postcode"] == "12043"
    assert rules.extract_emails(text) == ["m.testfeld@hv-testfeld.de"]


@pytest.mark.parametrize("text,expected", [
    ("Kontakt: max.muster(at)firma.de", ["max.muster@firma.de"]),
    ("max [at] firma [dot] de", ["max@firma.de"]),
    ("logo@2x.png und a@b.de", ["a@b.de"]),
    ("keine adresse hier", []),
])
def test_email_extraction(text, expected):
    assert rules.extract_emails(text) == expected


@pytest.mark.parametrize("email,generic", [
    ("info@firma.de", True), ("kontakt@firma.de", True), ("hausverwaltung@firma.de", True),
    ("max.muster@firma.de", False), ("m.muster@firma.de", False),
])
def test_email_classification(email, generic):
    assert rules.is_generic_email(email) is generic


def test_keywords_detected_with_source():
    page = {"url": "https://a.test/", "full_text": text_of("home.html")}
    out = rules.detect_keywords([page], KEYWORDS)
    assert out["metering_providers"]["value"] == ["Techem"]
    assert out["software_mentioned"]["value"] == ["casavi"]
    assert out["owner_portal"]["value"] == "mentioned"
    assert out["remote_reading_signal"]["source_url"] == "https://a.test/"
    assert "Funkzähler" in out["remote_reading_signal"]["matched_text"]
    assert out["hiring_signal"]["value"] == "mentioned"


def test_absent_keyword_is_not_mentioned_not_false():
    out = rules.detect_keywords([{"url": "u", "full_text": "Wir verwalten Häuser."}], KEYWORDS)
    assert out["remote_reading_signal"]["value"] == "not_mentioned"
    assert out["metering_providers"]["value"] is None


def test_funktion_does_not_trigger_remote_reading():
    out = rules.detect_keywords([{"url": "u", "full_text": "Die Funktion des Rundfunk."}], KEYWORDS)
    assert out["remote_reading_signal"]["value"] == "not_mentioned"


def test_domus_in_company_name_is_not_software():
    out = rules.detect_keywords([{"url": "u", "full_text": "Domus Hausverwaltung GmbH"}], KEYWORDS)
    assert out["software_mentioned"]["value"] is None


def good_record():
    empty = {"value": None, "source_url": None, "evidence_quote": None, "confidence": None}
    return {
        "company_id": "hv_1",
        "units_managed": {"value": 3200, "source_url": "https://a.test/", "evidence_quote": "rund 3.200 Wohneinheiten", "confidence": "high"},
        "management_types": {"value": ["WEG", "Miet"], "source_url": "https://a.test/", "evidence_quote": "WEG- und Mietverwaltung", "confidence": "high"},
        "employees": empty, "founding_year": empty, "other_cities": empty, "digital_maturity": empty, "company_summary": empty,
    }


def test_valid_record_passes():
    assert CompanyExtraction.model_validate(good_record()).units_managed.value == 3200


def test_value_without_evidence_fails():
    record = good_record()
    record["employees"] = {"value": 5, "source_url": None, "evidence_quote": None, "confidence": None}
    with pytest.raises(ValidationError):
        CompanyExtraction.model_validate(record)


def test_null_value_with_evidence_fails():
    record = good_record()
    record["employees"] = {"value": None, "source_url": "https://a.test/", "evidence_quote": "x", "confidence": "low"}
    with pytest.raises(ValidationError):
        CompanyExtraction.model_validate(record)


@pytest.mark.parametrize("key,value", [("units_managed", 900000), ("employees", 0), ("founding_year", 1200)])
def test_implausible_numbers_fail(key, value):
    record = good_record()
    record[key] = {"value": value, "source_url": "https://a.test/", "evidence_quote": "q", "confidence": "low"}
    with pytest.raises(ValidationError):
        CompanyExtraction.model_validate(record)


def test_unknown_field_fails():
    with pytest.raises(ValidationError):
        CompanyExtraction.model_validate({**good_record(), "extra": 1})


def test_web_designer_credit_is_not_the_company_name():
    text = "Realisierung\nLAYON Werbedesign & Kommunikation GmbH\nImpressum\nWohnungsbaugenossenschaft VORWAERTS eG\nBerlin"
    out = rules.extract_company_name(text, "https://www.wg-vorwaerts.de/")
    assert out["value"] == "Wohnungsbaugenossenschaft VORWAERTS eG"


def test_court_abbreviation_is_not_a_company_name():
    assert rules.extract_company_name("AG Berlin-Charlottenburg HRB 1 B", None) is None


def test_prose_about_betriebskosten_finds_no_person():
    line = "Die Gewobag informiert über Betriebskosten und alle wichtigen Themen rund um Ihr Zuhause in unserem Portal hier"
    assert rules.extract_team_roles(line) == []


def test_role_word_is_removed_from_person_name():
    text = "Vertreten durch: Geschäftsführerin Michelle Bastian\nRegistereintrag"
    assert [m["name"] for m in rules.extract_managers(text)] == ["Michelle Bastian"]


def test_article_is_not_a_first_name():
    assert rules.extract_team_roles("Die Gewobag\nBetriebskostenabrechnung") == []
    assert rules.extract_team_roles("Sie Begeisterung\nBetriebskostenabrechnung") == []


def test_sole_trader_name_comes_from_above_the_street():
    text = "Impressum\nHausverwaltung Erika Beispiel\nMusterstraße 1\n10115 Berlin"
    address = rules.extract_address(text)
    assert rules.name_before_address(text, address)["value"] == "Hausverwaltung Erika Beispiel"


def test_label_above_the_street_is_not_a_name():
    text = "Anschrift\nMusterstraße 1\n10115 Berlin"
    assert rules.name_before_address(text, rules.extract_address(text)) is None


@pytest.mark.parametrize("line,expected", [
    ("Ihr Immobilienverwalter – Schwarzer GmbH", "Schwarzer GmbH"),
    ("Vertretungsberechtigter: Braun und Hubertus GmbH", "Braun und Hubertus GmbH"),
    ("– Hausverwaltung Akbar –", "Hausverwaltung Akbar"),
    ("Geschäftsadresse: Hausverwaltung Bauriedel", "Hausverwaltung Bauriedel"),
    ("Herr Ingo Pries", "Ingo Pries"),
    ("Impressumsangaben Muster", "Impressumsangaben Muster"),
    ("ⓒ IVS HausRat GmbH", "IVS HausRat GmbH"),
    ("Impressum von Immobilien Hensel GmbH", "Immobilien Hensel GmbH"),
    ("Baurag Immobilienmanagement GmbH Immanuelkirchstr. 26", "Baurag Immobilienmanagement GmbH"),
    ("Hochheiden Hausverwaltung GmbH Berlin", "Hochheiden Hausverwaltung GmbH Berlin"),
    ("GmbH", ""),
    ("(C) 2026", ""),
    ("#Impressum", ""),
    ("IMMOBILIEN", ""),
])
def test_name_line_cleaning(line, expected):
    assert rules._clean_name_line(line) == expected


def test_name_continued_from_the_line_above():
    text = "Gehrung Immobilien-\nund Vermögensverwaltung mbH\nBerlin"
    assert rules.extract_company_name(text, None)["value"] == "Gehrung Immobilien- und Vermögensverwaltung mbH"


def test_slogan_is_not_a_company_name():
    assert rules.extract_company_name("Angaben gemäß § 5 DDG\nIhr Immobilienverwalter\nMusterstraße 1", None) is None


def test_team_role_needs_a_full_name():
    assert rules.extract_team_roles("Frau Olbrisch\nBetriebskostenabrechnung") == []
    assert rules.extract_team_roles("Lea Beispiel\nBetriebskostenabrechnung")[0]["name"] == "Lea Beispiel"


def test_invented_quote_is_detected():
    record = CompanyExtraction.model_validate(good_record())
    page = "Wir betreuen rund 3.200\nWohneinheiten in der WEG- und Mietverwaltung."
    assert missing_quotes(record, page) == []
    assert missing_quotes(record, "Eine ganz andere Seite.") == ["units_managed", "management_types"]
