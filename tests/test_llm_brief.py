import pytest

from leadengine.schemas import CompanyExtraction, missing_quotes
from leadengine.stages import llm_brief as lb


def doc(text, kind="home"):
    return {"company_id": "hv_1", "website": "https://a.test/", "pages": [{"kind": kind, "url": "https://a.test/", "full_text": text}]}


def keys(text):
    return [c["key"] for c in lb.candidate_lines(doc(text))]


@pytest.mark.parametrize("line,expected", [
    ("Wir betreuen rund 3.200 Wohneinheiten in Berlin.", ["u"]),
    ("Unsere 25 engagierten Mitarbeiter sind für Sie da.", ["e"]),
    ("Gegründet im Jahr 1998 in Berlin.", ["f"]),
    ("Wir sind auch in Potsdam aktiv.", ["c"]),
    ("Schöne 3-Zimmer Wohnungen zur Miete", []),
    ("6 Wohnungen im Altbau", []),
    ("© 2024 Muster GmbH", []),
    ("Seit dem Gesetz von 2020 gilt die Pflicht", []),
])
def test_candidate_detection(line, expected):
    assert keys(line) == expected


def test_impressum_is_skipped():
    assert lb.candidate_lines(doc("Gegründet 1998", kind="impressum")) == []


def test_largest_unit_count_comes_first():
    found = lb.candidate_lines(doc("WEG, 78 Wohnungen\nInsgesamt 4.000 Wohnungen"))
    assert found[0]["size"] == 4000


def test_long_line_quote_is_verbatim_and_short():
    line = "x" * 300 + " rund 3.200 Wohneinheiten " + "y" * 300
    found = lb.candidate_lines(doc(line))
    assert len(found[0]["text"]) <= 190 and found[0]["text"] in line


def test_management_types_detected():
    out = lb.detect_management(doc("WEG-Verwaltung\nMietverwaltung\nImpressum"))
    assert out["value"] == ["WEG", "Miet"] and " … " in out["evidence_quote"]


def test_no_management_is_null():
    assert lb.detect_management(doc("Wir bauen Häuser."))["value"] is None


def test_digital_levels():
    rules_soft = {"software_mentioned": {"hits": [{"source_url": "u", "matched_text": "casavi"}]}}
    assert lb.detect_digital(doc("x"), rules_soft)["value"] == "high"
    assert lb.detect_digital(doc("Unsere Mieter-App"), {})["value"] == "high"
    portal = {"owner_portal": {"value": "mentioned", "source_url": "u", "matched_text": "Eigentümerportal"}}
    assert lb.detect_digital(doc("x"), portal)["value"] == "medium"
    assert lb.detect_digital(doc("x"), {})["value"] is None


def test_parse_decision():
    cid, picks = lb.parse_decision("hv_1 u=1:3200 e=2:25~ c=3:Potsdam,Hamburg")
    assert cid == "hv_1" and picks["u"] == {"index": 1, "value": "3200", "confidence": "high"}
    assert picks["e"]["confidence"] == "medium" and picks["c"]["value"] == "Potsdam,Hamburg"


def test_build_record_copies_quote_and_validates():
    text = "Wir betreuen rund 3.200 Wohneinheiten.\nWEG-Verwaltung"
    d = doc(text)
    found = lb.candidate_lines(d)
    record = lb.build_record("hv_1", d, {}, found, {"u": {"index": 1, "value": "3200", "confidence": "high"}})
    parsed = CompanyExtraction.model_validate(record)
    assert parsed.units_managed.value == 3200
    assert parsed.units_managed.evidence_quote == "Wir betreuen rund 3.200 Wohneinheiten."
    assert missing_quotes(parsed, text) == []


def test_wrong_line_type_is_rejected():
    d = doc("Gegründet 1998")
    with pytest.raises(ValueError):
        lb.build_record("hv_1", d, {}, lb.candidate_lines(d), {"u": {"index": 1, "value": "5", "confidence": "high"}})


def test_no_decision_keeps_reviewed_fields_null():
    d = doc("Wir betreuen rund 3.200 Wohneinheiten.")
    record = lb.build_record("hv_1", d, {}, lb.candidate_lines(d), {})
    assert record["units_managed"]["value"] is None
