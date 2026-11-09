"""Stage 5: pick the most relevant person and label the email status."""

import re
from urllib.parse import quote_plus

import pandas as pd

from leadengine.normalize import normalize_domain
from leadengine.store import read_candidates, read_json

TITLES = re.compile(r"\b(?:Dr|Prof|Dipl)\.?(?:-[A-Za-zäöü]+\.?)?\s+")
PARTICLES = {"von", "van", "de", "zu"}


def transliterations(word: str) -> set[str]:
    word = word.lower().replace("ß", "ss")
    return {word.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})),
            word.translate(str.maketrans({"ä": "a", "ö": "o", "ü": "u"}))}


def split_name(name: str) -> tuple[str, str] | None:
    parts = [p for p in TITLES.sub("", name).split() if p.lower() not in PARTICLES]
    return (parts[0], parts[-1]) if len(parts) >= 2 else None


PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "first-last": lambda f, l: f"{f}-{l}",
    "last": lambda f, l: l,
}


def detect_pattern(local: str, name: str) -> str | None:
    """Return the pattern key if the local part is built from this person's name."""
    pieces = split_name(name)
    if not pieces:
        return None
    for first in transliterations(pieces[0]):
        for last in transliterations(pieces[1]):
            for key, build in PATTERNS.items():
                if build(first, last) == local.lower():
                    return key
    return None


NOT_A_PERSON_TOKEN = re.compile(
    r"^(?:info|kontakt|service|center|team|office|mail|post|mieter|kunden|verwaltung|hausverwaltung|immobilien|weg|"
    r"buchhaltung|technik|vermietung|abrechnung|berlin|hv|hello|hallo|support|admin|web|fm|objekt|notfall)$", re.I)


def pattern_from_shape(local: str) -> str | None:
    """first.last or f.last, judged only from the mailbox's form; never for role words."""
    parts = local.lower().split(".")
    if len(parts) != 2 or any(NOT_A_PERSON_TOKEN.match(p) for p in parts):
        return None
    if re.fullmatch(r"[a-zäöü]{2,}", parts[0]) and re.fullmatch(r"[a-zäöü]{3,}", parts[1]):
        return "first.last"
    if re.fullmatch(r"[a-z]", parts[0]) and re.fullmatch(r"[a-zäöü]{3,}", parts[1]):
        return "f.last"
    return None


def infer_email(name: str, pattern: str, domain: str) -> str | None:
    pieces = split_name(name)
    if not pieces:
        return None
    first = sorted(transliterations(pieces[0]))[0]
    last = sorted(transliterations(pieces[1]))[0]
    # Prefer the 'ae' spelling: German mailboxes rarely drop the umlaut's e.
    first = pieces[0].lower().replace("ß", "ss").translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"}))
    last = pieces[1].lower().replace("ß", "ss").translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"}))
    return f"{PATTERNS[pattern](first, last)}@{domain}"


def choose_person(rules: dict) -> dict | None:
    """Rank 1 Geschäftsführer/Inhaber, rank 2 WEG/Miet lead, rank 3 Heizkosten lead."""
    people = [{**m, "rank": 1} for m in rules.get("managers", [])] + list(rules.get("team_people", []))
    return min(people, key=lambda p: p["rank"]) if people else None


def resolve_contact(rules: dict) -> dict:
    """Return contact_name, role, email and the always-present email_status."""
    person = choose_person(rules)
    domain = normalize_domain(rules.get("website"))
    emails = [e for e in rules.get("emails", []) if normalize_domain("//" + e["email"].split("@")[1]) == domain]
    generic = next((e for e in emails if e["generic"]), None)
    result = {
        "contact_name": person["name"] if person else None,
        "contact_role": person["role"] if person else None,
        "contact_name_source_url": person["source_url"] if person else None,
        "contact_name_confidence": person["confidence"] if person else None,
        "contact_email": None, "contact_email_source_url": None, "contact_email_confidence": None,
        "generic_email": generic["email"] if generic else None,
        "email_status": "generic_only" if generic else "none",
    }
    if not person:
        return result

    for e in emails:
        if not e["generic"] and detect_pattern(e["email"].split("@")[0], person["name"]):
            return {**result, "contact_email": e["email"], "contact_email_source_url": e["source_url"],
                    "contact_email_confidence": "high", "email_status": "found_on_site"}

    # Infer only from a pattern seen for a different named person on this domain.
    known = [p["name"] for p in rules.get("managers", []) + rules.get("team_people", []) if p["name"] != person["name"]]
    for e in emails:
        if e["generic"]:
            continue
        for other in known:
            pattern = detect_pattern(e["email"].split("@")[0], other)
            if pattern and pattern != "last":
                inferred = infer_email(person["name"], pattern, domain)
                return {**result, "contact_email": inferred, "contact_email_source_url": e["source_url"],
                        "contact_email_confidence": "low", "email_status": "pattern_inferred"}

    # No matching name, but the mailbox has the shape of a personal address on this domain.
    for e in emails:
        pattern = None if e["generic"] else pattern_from_shape(e["email"].split("@")[0])
        if pattern:
            inferred = infer_email(person["name"], pattern, domain)
            return {**result, "contact_email": inferred, "contact_email_source_url": e["source_url"],
                    "contact_email_confidence": "low", "email_status": "pattern_inferred"}
    return result


def search_urls(name: str | None, company: str | None) -> dict:
    if not name:
        return {"linkedin_search_url": None, "xing_search_url": None}
    terms = quote_plus(" ".join(p for p in (name, company) if p))
    return {"linkedin_search_url": f"https://www.linkedin.com/search/results/people/?keywords={terms}",
            "xing_search_url": f"https://www.xing.com/search/members?keywords={terms}"}


def run(ctx) -> dict:
    rows = []
    for company in read_candidates(ctx).to_dict("records"):
        rules = read_json(ctx.paths.rules / f"{company['company_id']}.json")
        contact = resolve_contact(rules) if rules else {"contact_name": None, "email_status": "none"}
        company_name = (rules or {}).get("company_name", {}).get("value") or company.get("company_name")
        rows.append({"company_id": company["company_id"], "crawled": rules is not None, **contact,
                     **search_urls(contact.get("contact_name"), company_name if isinstance(company_name, str) else None),
                     "linkedin_profile_url": None})
    df = pd.DataFrame(rows)
    # Public profiles found by web search for Tier A contacts. Kept local, never in the repo.
    profiles = read_json(ctx.paths.processed / "linkedin_profiles.json") or {}
    df["linkedin_profile_url"] = df["company_id"].map(profiles).astype(object)
    df.to_csv(ctx.paths.processed / "contacts.csv", index=False, encoding="utf-8-sig")
    return {"companies": len(df), "named_contact": int(df["contact_name"].notna().sum()),
            **{f"email_{k}": int(v) for k, v in df["email_status"].value_counts().items()}}
