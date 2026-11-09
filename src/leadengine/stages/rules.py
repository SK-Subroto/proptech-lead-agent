"""Stage 3a: extract structured fields from crawled pages with code only."""

import logging
import re

import pandas as pd

from leadengine.normalize import normalize_domain, normalize_name
from leadengine.store import read_candidates, read_json, write_json

log = logging.getLogger("leadengine")

EMAIL = re.compile(r"[a-z0-9][a-z0-9._%+\-]*@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}", re.I)
OBFUSCATED_AT = re.compile(r"\s*(?:\(at\)|\[at\]|\{at\}|\(ät\)|\s+at\s+|\s\[\s*@\s*\]\s)\s*", re.I)
OBFUSCATED_DOT = re.compile(r"\s*(?:\(dot\)|\[dot\]|\(punkt\)|\[punkt\])\s*", re.I)
BAD_EMAIL_END = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)
BAD_EMAIL_DOMAIN = re.compile(r"(sentry|wixpress|example\.(com|de|org)|domain\.(de|com)|mustermann)", re.I)

PHONE = re.compile(r"(?<![\d/])(?:\+49|0049|\(?0)[\s\d()/\-]{6,20}\d")
PHONE_LABEL = re.compile(r"(?:tel(?:efon|\.)?|fon|phone)\s*:?\s*", re.I)
FAX_LABEL = re.compile(r"fax", re.I)
REGISTER = re.compile(r"\bHR\s?[AB]\s*(?:Nr\.?\s*)?\d{2,7}(?:\s?[A-Z])?\b")

LEGAL_FORMS = [
    ("GmbH & Co. KG", r"gmbh\s*(?:&|und)\s*co\.?\s*kg"),
    ("UG (haftungsbeschränkt)", r"\bug\s*\(?haftungsbeschr[äa]nkt\)?|\bug\b"),
    ("GmbH", r"\bgmbh\b|gesellschaft mit beschr[äa]nkter haftung"),
    ("AG", r"\baktiengesellschaft\b|\bag\b"),
    ("OHG", r"\bohg\b"),
    ("KG", r"\bkg\b"),
    ("GbR", r"\bgbr\b"),
    ("e.K.", r"\be\.\s?k(?:fr|fm)?\.?(?=\s|$|,)"),
    ("eG", r"\beg\b"),
]

MANAGER_LABEL = re.compile(
    r"(?:gesch[äa]ftsf[üu]hr(?:er(?:in)?|ung|ende[rs]?\s+gesellschafter(?:in)?)"
    r"|inhaber(?:in)?|vertretungsberechtigt(?:e[rs]?)?(?:\s+gesellschafter(?:in)?)?"
    r"|vertreten\s+durch(?:\s+die\s+gesch[äa]ftsf[üu]hr(?:er(?:in)?|ung))?|vorstand)"
    r"\s*(?:\(in\)|:in)?\s*[:\-–]?\s*",
    re.I,
)
NAME = re.compile(
    r"(?:(?:Dr|Prof|Dipl)\.?(?:-[A-Za-zäöü]+\.?)?\s+)*"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
    r"(?:\s+(?:von|van|de|zu)\b)?"
    r"(?:\s+[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?){1,2}"
)
NOT_A_NAME = re.compile(
    r"gmbh|gesellschaft|straße|strasse|berlin|telefon|telefax|handelsregister|registergericht|"
    r"amtsgericht|verantwortlich|angaben|umsatzsteuer|kontakt|impressum|hausverwaltung|immobilien|"
    r"verwaltung|deutschland|sitz|die\s+gesch|supervisory|board|management|aufsichtsrat|vorsitz|\bgmb\b|"
    r"^(?:die|der|das|den|dem|unser\w*|ihre?|sie|wir|bei|f[üu]r|mit|von|alle|neue?)\s",
    re.I,
)
SALUTATION = re.compile(
    r"^(?:(?:Frau|Herr|Fr\.|Hr\.|Dipl\.?-?[A-Za-zäöü]*\.?|Gesch[äa]ftsf[üu]hrer(?:in)?|Inhaber(?:in)?|Vorstand)\s+)+")
ROLE_PATTERNS = [
    (2, re.compile(r"leit(?:ung|er(?:in)?)\s+(?:der\s+)?(?:weg|miet|haus|immobilien|objekt)[-\w]*|(?:weg|miet)[-\w]*leit(?:ung|er(?:in)?)|prokurist(?:in)?", re.I)),
    (3, re.compile(r"leit\w+\s+(?:der\s+)?(?:heizkosten|betriebskosten|nebenkosten)\w*|(?:heizkosten|betriebskosten|nebenkosten)\w*(?:abrechnung|abrechner\w*|leit\w+)", re.I)),
]
# A street is any capitalised text ending in a house number, directly before a postcode.
ADDRESS = re.compile(
    r"([A-ZÄÖÜ][\wäöüßÄÖÜ.\- ]{1,50}?[a-zäöüß.]\s*\d{1,4}\s*[a-zA-Z]?(?:\s*[-/]\s*\d{1,4}\s*[a-zA-Z]?)?)"
    r"\s*[,\n|·•]+\s*(?:D\s*-\s*)?(\d{5})\s+([A-ZÄÖÜ][\wäöüß\- ]{1,30})",
)
GENERIC_LOCALS = re.compile(
    r"^(info|infos|kontakt|contact|office|mail|email|e-mail|post|postmaster|service|verwaltung|hausverwaltung|"
    r"immobilien|buero|büro|empfang|zentrale|datenschutz|impressum|webmaster|admin|bewerbung|jobs?|karriere|"
    r"rechnung(en)?|buchhaltung|vermietung|technik|notfall|support|team|hello|hallo|anfrage|anfragen|weg|miete|"
    r"mietverwaltung|hv|hausmeister|sekretariat|kundenservice|hv-?info|info-?berlin|berlin|abrechnung|heizkosten|betriebskosten|"
    r"objektbetreuung|schaden|schadensmeldung|reparatur|sales|presse|marketing|hr|personal)([._\-]?\d*)$",
    re.I,
)


def deobfuscate(text: str) -> str:
    return OBFUSCATED_DOT.sub(".", OBFUSCATED_AT.sub("@", text))


def extract_emails(text: str, mailtos: list[str] | None = None) -> list[str]:
    found = list(mailtos or [])
    found += [m.group(0) for m in EMAIL.finditer(deobfuscate(text))]
    clean = []
    for email in found:
        email = email.strip().strip(".").lower()
        if EMAIL.fullmatch(email) and not BAD_EMAIL_END.search(email) and not BAD_EMAIL_DOMAIN.search(email):
            if email not in clean:
                clean.append(email)
    return clean


def is_generic_email(email: str) -> bool:
    return bool(GENERIC_LOCALS.match(email.split("@")[0]))


def extract_phones(text: str) -> list[str]:
    """Numbers after a telephone label first; fax numbers are ignored."""
    labelled, other = [], []
    for match in PHONE.finditer(text):
        number = re.sub(r"\s+", " ", match.group(0)).strip()
        if len(re.sub(r"\D", "", number)) < 8:
            continue
        before = text[max(0, match.start() - 14):match.start()]
        if FAX_LABEL.search(before):
            continue
        target = labelled if PHONE_LABEL.search(before) else other
        if number not in labelled + other:
            target.append(number)
    return labelled + other


def extract_register(text: str) -> str | None:
    match = REGISTER.search(text)
    return re.sub(r"\s+", " ", match.group(0)) if match else None


def extract_legal_form(text: str) -> str | None:
    for label, pattern in LEGAL_FORMS:
        if re.search(pattern, text, re.I):
            return label
    return None


def extract_managers(text: str) -> list[dict]:
    """Names following a Geschäftsführer/Inhaber-style label on the same or next line."""
    managers = []
    seen = set()
    for label in MANAGER_LABEL.finditer(text):
        window = text[label.end():label.end() + 140]
        lines = window.split("\n")
        scope = lines[0] if lines[0].strip() else ""
        if len(lines) > 1 and (not scope.strip() or scope.rstrip().endswith((",", "und", "&"))):
            scope += " " + lines[1]
        if not scope.strip() and len(lines) > 2:
            scope = lines[2]
        scope = re.split(r"\b(?:Sitz|Tel|Telefon|Registergericht|Handelsregister|HRB|E-Mail|USt)\b", scope)[0]
        for part in re.split(r",|;|\bund\b|&|/", scope):
            match = NAME.search(part)
            if not match or NOT_A_NAME.search(match.group(0)):
                continue
            name = SALUTATION.sub("", re.sub(r"\s+", " ", match.group(0)).strip())
            if len(name.split()) >= 2 and name not in seen:
                seen.add(name)
                role = re.sub(r"\s+", " ", label.group(0)).strip(" :-–")
                managers.append({"name": name, "role": role, "matched_text": label.group(0) + part.strip()})
    return managers


def extract_team_roles(text: str) -> list[dict]:
    """People with a WEG/Miet/Heizkosten leadership role on team pages."""
    people = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if len(line) > 80:  # role lines on team pages are short; long lines are prose
            continue
        for rank, pattern in ROLE_PATTERNS:
            role = pattern.search(line)
            if not role:
                continue
            nearby = [line] + lines[max(0, i - 1):i] + lines[i + 1:i + 2]
            for candidate in nearby:
                match = NAME.search(candidate)
                clean = SALUTATION.sub("", match.group(0).strip()) if match else ""
                if match and len(clean.split()) >= 2 and not NOT_A_NAME.search(clean):
                    people.append({"name": clean, "role": role.group(0).strip(),
                                   "rank": rank, "matched_text": line[:200]})
                    break
            break
    return people


def extract_address(text: str) -> dict | None:
    match = ADDRESS.search(text)
    if not match:
        return None
    street = re.sub(r"\s+", " ", match.group(1)).strip()
    city = match.group(3).split("\n")[0].strip()
    return {"street": street, "postcode": match.group(2), "city": city,
            "value": f"{street}, {match.group(2)} {city}", "matched_text": match.group(0)[:200]}


NAME_LINE_FORMS = "|".join(p for _, p in LEGAL_FORMS[:4]) + r"|\bmbh\b|\bohg\b|\bgbr\b|\bkg\b|\beg\b|e\.\s?k\."
NOT_COMPANY_LINE = re.compile(
    r"registergericht|handelsregister|amtsgericht|\bhr[ab]\b|\bhaftung\b|§|^ag\b|^aktiengesellschaft\b|vertreten|gesch[äa]ftsf[üu]hr|"
    r"tochter|copyright|©|sitz\b|werbe|design|agentur|kommunikation|realisierung|foto|hosting|marketing|webentwicklung|"
    r"pdf|brosch[üu]re",
    re.I,
)


GENERIC_NAME_LINE = re.compile(
    r"(?:ihr|ihre|unser\w*|willkommen|impressum|anschrift|adresse|postanschrift|kontakt\w*|firma|name|inhaber\w*|sitz|"
    r"telefon|tel\b|e-?mail|angaben|anbieter|verantwortlich\w*|herausgeber|diensteanbieter|start(?:seite)?|home|"
    r"men[üu]|deutsch|english|[üu]ber uns|leistungen|datenschutz|agb|markenzeichen|service point|gesch[äa]ftsstelle)\b",
    re.I,
)


NAME_PREFIX = re.compile(
    r"^(?:gesch[äa]ftsadresse|vertretungsberechtigte[rn]?|firmierung|firma|inh(?:aber(?:in)?)?\.?|herr|frau)\s*:?\s+", re.I)
NOT_A_COMPANY_VALUE = {"gmbh", "gbr", "ug", "kg", "ag", "ohg", "eg", "büro", "buero", "immobilien", "impressum"}
PUNCTUATION = " ,:;#–—-()©ⓒ"
NAME_THEN_STREET = re.compile(
    r"(.+?(?:gmbh\s*&\s*co\.?\s*kg|gmbh|mbh|\bug\b|\bag\b|\bkg\b|\bohg\b|\bgbr\b|e\.\s?k\.|\beg\b)\.?)\s+[A-ZÄÖÜ].*\d", re.I)


def _clean_name_line(line: str) -> str:
    """Strip headings and labels from a name line; an empty result means it was not a name."""
    value = re.sub(r"^impressum\b(?:\s+(?:der|des|von)\b)?\s*[-–—|:]?\s*", "", line.strip(), flags=re.I)
    parts = [p.strip() for p in re.split(r" – | \| ", value) if p.strip()]
    value = next((p for p in parts if re.search(NAME_LINE_FORMS, p, re.I)), parts[0] if parts else "")
    value = NAME_PREFIX.sub("", value.strip(PUNCTUATION)).strip(PUNCTUATION)
    with_street = NAME_THEN_STREET.match(value)
    if with_street:
        value = with_street.group(1)
    letters = re.findall(r"[A-Za-zÄÖÜäöüß]", value)
    if len(letters) < 3 or value.lower() in NOT_A_COMPANY_VALUE or GENERIC_NAME_LINE.match(value):
        return ""
    return value


def _name_value(lines: list[str], index: int) -> str:
    """A line starting with 'und' continues the name from the line above."""
    value = _clean_name_line(lines[index])
    if value.startswith("und ") and index > 0:
        return _clean_name_line(f"{lines[index - 1].strip()} {value}")
    return value


def extract_company_name(text: str, website: str | None = None) -> dict | None:
    """Prefer a legal-form line matching the site's domain, then the first after the heading."""
    lines = text.splitlines()
    candidates = [(i, line) for i, line in enumerate(lines)
                  if 4 <= len(line) <= 90 and re.search(NAME_LINE_FORMS, line, re.I) and not NOT_COMPANY_LINE.search(line)
                  and _name_value(lines, i)]
    if candidates:
        domain = normalize_domain(website) or ""
        tokens = [t for t in re.split(r"[-.]", domain.split(".")[0]) if len(t) >= 4]
        for i, line in candidates:
            if any(t in normalize_name(line).replace(" ", "") for t in tokens):
                return {"value": _name_value(lines, i), "confidence": "high", "matched_text": line}
        headings = [i for i, line in enumerate(lines) if re.fullmatch(r"impressum:?", line.strip(), re.I)]
        after = [c for c in candidates if headings and c[0] > headings[-1]]
        i, line = (after or candidates)[0]
        return {"value": _name_value(lines, i), "confidence": "medium", "matched_text": line}
    for i, line in enumerate(lines):
        if re.search(r"angaben\s+gem[äa](?:ß|ss)|diensteanbieter|anbieter\s*:", line, re.I) and i + 1 < len(lines):
            next_line = lines[i + 1]
            value = _clean_name_line(next_line)
            if 3 <= len(next_line) <= 80 and value and not NOT_COMPANY_LINE.search(next_line):
                return {"value": value, "confidence": "medium", "matched_text": next_line}
    return None


def name_before_address(text: str, address: dict) -> dict | None:
    """Sole traders have no legal form, but their name usually sits right above the street."""
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if address["street"] in re.sub(r"\s+", " ", line)), None)
    if index is None:
        return None
    for line in reversed(lines[max(0, index - 2):index]):
        clean = _clean_name_line(line)
        junk = re.search(r"ust\b|ust\.|steuer|webseite|anmelden|vorsitz", line, re.I) or clean[:1].islower() \
            or normalize_name(clean) in ("hausverwaltung", "hausverwaltung und immobilien")
        if clean and not junk and len(clean) <= 80 and not NOT_COMPANY_LINE.search(line) and not line.rstrip().endswith(":"):
            return {"value": clean, "confidence": "low", "matched_text": line}
    return None


def field(value, source_url, matched_text, confidence):
    return {"value": value, "source_url": source_url, "matched_text": matched_text, "confidence": confidence}


def _snippet(text: str, start: int, end: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - 40):end + 40]).strip()


def detect_keywords(pages: list[dict], keywords: dict) -> dict:
    """R3.4: a keyword that is not found is 'not_mentioned', never false."""
    def first_hit(pattern):
        for page in pages:
            match = re.search(pattern, page["full_text"], re.I)
            if match:
                return page["url"], _snippet(page["full_text"], match.start(), match.end())
        return None

    def named(group):
        names, hits = [], []
        for name, pattern in keywords[group].items():
            hit = first_hit(pattern)
            if hit:
                names.append(name)
                hits.append({"name": name, "source_url": hit[0], "matched_text": hit[1]})
        return {"value": names or None, "hits": hits,
                "source_url": hits[0]["source_url"] if hits else None,
                "confidence": "medium" if hits else None}

    def flag(key):
        hit = first_hit(keywords[key])
        if hit:
            return field("mentioned", hit[0], hit[1], "medium")
        return field("not_mentioned", None, None, None)

    return {
        "metering_providers": named("metering_providers"),
        "software_mentioned": named("software"),
        "owner_portal": flag("owner_portal"),
        "remote_reading_signal": flag("remote_reading"),
        "hiring_signal": flag("hiring"),
        "management_evidence": flag("management_evidence"),
    }


def extract_company(doc: dict, keywords: dict) -> dict:
    pages = doc["pages"]
    impressum = next((p for p in pages if p["kind"] == "impressum"), None)
    source = impressum or next((p for p in pages if p["kind"] == "kontakt"), None) or pages[0]
    text, url = source["full_text"], source["url"]
    confidence = "high" if impressum else "medium"

    out = {"company_id": doc["company_id"], "website": doc["website"], "impressum_url": impressum["url"] if impressum else None}

    address = extract_address(text)
    name = extract_company_name(text, doc.get("website")) if impressum else None
    if impressum and not name and address:
        name = name_before_address(text, address)
    out["company_name"] = field(name["value"], url, name["matched_text"], name["confidence"]) if name else field(None, None, None, None)

    legal = extract_legal_form(name["value"]) if name else None
    out["legal_form"] = field(legal, url, name["value"], confidence) if legal else field(None, None, None, None)

    out["address"] = field(address["value"], url, address["matched_text"], confidence) if address else field(None, None, None, None)
    out["postcode"] = field(address["postcode"], url, address["matched_text"], confidence) if address else field(None, None, None, None)
    out["address_city"] = address["city"] if address else None
    out["address_street"] = address["street"] if address else None

    phones = extract_phones(text)
    out["phone"] = field(phones[0], url, phones[0], confidence) if phones else field(None, None, None, None)

    register = extract_register(text)
    out["handelsregister_no"] = field(register, url, register, confidence) if register else field(None, None, None, None)

    out["managers"] = [{**m, "source_url": url, "confidence": confidence} for m in extract_managers(text)]

    out["team_people"] = []
    for page in pages:
        if page["kind"] in ("team", "ueber_uns", "kontakt"):
            out["team_people"] += [{**p, "source_url": page["url"], "confidence": "medium"} for p in extract_team_roles(page["full_text"])]

    emails = {}
    for page in pages:
        for email in extract_emails(page["full_text"], page.get("mailtos")):
            emails.setdefault(email, page["url"])
    out["emails"] = [{"email": e, "source_url": u, "generic": is_generic_email(e)} for e, u in emails.items()]

    out.update(detect_keywords(pages, keywords))
    out["pages_used"] = [p["url"] for p in pages]
    return out


def lacks_other_evidence(company: dict) -> bool:
    """Absence from a few pages proves little, so only exclude with nothing else in favour."""
    name = company.get("match_name")
    has_name_evidence = isinstance(name, str) and re.search("verwalt", name, re.I)
    return not (has_name_evidence or company.get("self_managed_landlord") is True
                or company.get("osm_office_tag") == "property_management")


def run(ctx) -> dict:
    candidates = read_candidates(ctx)
    extracted, excluded = 0, []
    for company in candidates.to_dict("records"):
        doc = read_json(ctx.paths.pages / f"{company['company_id']}.json")
        if not doc:
            continue
        result = extract_company(doc, ctx.keywords)
        write_json(ctx.paths.rules / f"{company['company_id']}.json", result)
        extracted += 1
        manual = (ctx.keywords.get("manual_exclusions") or {}).get(company["company_id"])
        if manual:
            excluded.append({"company_id": company["company_id"], "exclusion_reason": manual})
        elif result["management_evidence"]["value"] == "not_mentioned" and lacks_other_evidence(company):
            excluded.append({"company_id": company["company_id"], "exclusion_reason": "no_management_evidence_on_site"})

    pd.DataFrame(excluded, columns=["company_id", "exclusion_reason"]).to_csv(
        ctx.paths.processed / "excluded_after_crawl.csv", index=False, encoding="utf-8-sig")
    return {"extracted": extracted, "no_management_evidence": len(excluded)}
