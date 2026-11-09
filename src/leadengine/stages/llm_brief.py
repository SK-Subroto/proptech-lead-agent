"""Cheap Stage 3b: code pulls candidate lines, Claude Code only picks the right one.

    python -m leadengine llm brief      writes _briefs/brief.txt and candidates.json
    (Claude Code writes _briefs/decisions.txt)
    python -m leadengine llm apply      expands decisions into validated JSON records

decisions.txt holds one line per company that has a stated value. Numbers refer
to the numbered candidate lines in the brief. A trailing ~ means medium confidence:

    hv_abc u=1:3200 e=2:25~ f=3:1998 c=4:Potsdam,Hamburg

Code copies each quote from the crawled page, so a quote can never be mistyped.
Management type and digital maturity are detected by code. A company with no
decision keeps null for the reviewed fields.
"""

import json
import re

from leadengine.schemas import CompanyExtraction
from leadengine.stages.llm import remaining
from leadengine.store import read_json

NUL = {"value": None, "source_url": None, "evidence_quote": None, "confidence": None}
MAX_QUOTE = 190
LIMITS = {"u": 3, "e": 2, "f": 2, "c": 1}
FIELD_BY_KEY = {"u": "units_managed", "e": "employees", "f": "founding_year", "c": "other_cities"}

CANDIDATE_PATTERNS = {
    "u": re.compile(r"\d[\d.,]*\s*\+?\s*(?:[\w-]+\s+){0,2}?(?:wohn-?\s*(?:und\s+gewerbe)?einheiten|mieteinheiten|verwaltungseinheiten|einheiten|wohnungen)", re.I),
    "e": re.compile(r"\b\d{1,5}\s*\+?\s*(?:[\w-]+\s+){0,2}?(?:mitarbeiter\w*|mitarbeitende\w*|kolleg\w+|angestellte\w*|besch[äa]ftigte\w*|fachkr[äa]fte\w*)|team\s+(?:von|aus|mit)\s+(?:\w+\s+)?\d+", re.I),
    "f": re.compile(r"(?:gegr[üu]ndet|gr[üu]ndung|gr[üu]ndungsjahr|bereits\s+seit|seit|since|founded|established)\D{0,30}\b(1[89]\d{2}|20[0-2]\d)\b", re.I),
    "c": re.compile(r"(?:auch\s+in|standorte?\s+in|niederlassung(?:en)?\s+in|b[üu]ros?\s+in|sowie\s+in)\s+[A-ZÄÖÜ]\w+"),
}
BAD_LINE = {
    "u": re.compile(r"zimmer|m²|qm\b|quadratmeter|€|euro\b|prozent|%", re.I),
    "e": re.compile(r"€|euro\b|prozent|%", re.I),
    "f": re.compile(r"©|copyright|\bstand\b|zuletzt|§|gesetz|verordnung|urteil", re.I),
    "c": re.compile(r"$^"),
}
MANAGEMENT_PATTERNS = {
    "WEG": re.compile(r"weg[-\s]?verwalt|wohnungseigentumsverwalt|eigentumsverwaltung|wohnungseigent[üu]merg", re.I),
    "Miet": re.compile(r"miet(?:haus|shaus|en|er)?[-\s]?verwalt|zinshausverwalt", re.I),
    "SE": re.compile(r"sondereigentum|\bsev\b|\bse[-\s]verwaltung", re.I),
    "Gewerbe": re.compile(r"gewerbe(?:immobilien|objekt\w*)?[-\s]?verwalt", re.I),
}
APP = re.compile(r"\b(?:mieter|kunden|eigent[üu]mer)[-\s]?app\b", re.I)


def _snippet(line: str, start: int, end: int) -> str:
    """A verbatim piece of the line around the match, short enough to quote."""
    if len(line) <= MAX_QUOTE:
        return line.strip()
    left = max(0, start - 60)
    return line[left:left + MAX_QUOTE].strip()


def _biggest_number(text: str) -> int:
    numbers = [int(n.replace(".", "").replace(",", "")) for n in re.findall(r"\d[\d.,]*\d|\d", text)
               if len(n.replace(".", "").replace(",", "")) <= 7]
    return max(numbers, default=0)


def candidate_lines(doc: dict) -> list[dict]:
    """Numbered lines that may state a KPI; the largest unit counts come first."""
    found = {key: [] for key in CANDIDATE_PATTERNS}
    seen = set()
    for page in doc["pages"]:
        if page["kind"] == "impressum":
            continue
        for line in page["full_text"].splitlines():
            for key, pattern in CANDIDATE_PATTERNS.items():
                match = pattern.search(line)
                if not match or BAD_LINE[key].search(line):
                    continue
                size = _biggest_number(match.group(0))
                if key == "u" and size < 30:
                    continue
                text = _snippet(line, match.start(), match.end())
                if (key, text.lower()) not in seen:
                    seen.add((key, text.lower()))
                    found[key].append({"key": key, "text": text, "url": page["url"], "size": size})
    out = []
    for key in ("u", "e", "f", "c"):
        out += sorted(found[key], key=lambda c: -c["size"])[:LIMITS[key]]
    return out


def detect_management(doc: dict) -> dict:
    """Types named on the site. Each type's own line is part of the quote."""
    parts, types, url = [], [], None
    for name, pattern in MANAGEMENT_PATTERNS.items():
        for page in doc["pages"]:
            hit = next((m for m in (pattern.search(l) for l in page["full_text"].splitlines()) if m), None)
            if hit:
                line = hit.string
                types.append(name)
                url = url or page["url"]
                parts.append(line[max(0, hit.start() - 10):hit.end() + 20].strip() if len(line) > 45 else line.strip())
                break
    if not types:
        return dict(NUL)
    return {"value": types, "source_url": url, "evidence_quote": " … ".join(parts)[:195], "confidence": "medium"}


def detect_digital(doc: dict, rules: dict) -> dict:
    """high: named software or a tenant/owner app. medium: a portal. Otherwise null."""
    software = rules.get("software_mentioned") or {}
    if software.get("hits"):
        hit = software["hits"][0]
        return {"value": "high", "source_url": hit["source_url"], "evidence_quote": hit["matched_text"][:190], "confidence": "medium"}
    for page in doc["pages"]:
        for line in page["full_text"].splitlines():
            match = APP.search(line)
            if match:
                return {"value": "high", "source_url": page["url"], "evidence_quote": _snippet(line, match.start(), match.end()), "confidence": "medium"}
    portal = rules.get("owner_portal") or {}
    if portal.get("value") == "mentioned":
        return {"value": "medium", "source_url": portal["source_url"], "evidence_quote": portal["matched_text"][:190], "confidence": "medium"}
    return dict(NUL)


def briefs_dir(ctx):
    path = ctx.paths.llm / "_briefs"
    path.mkdir(exist_ok=True)
    return path


def write_brief(ctx) -> None:
    candidates, lines, with_lines = {}, [], 0
    for cid in remaining(ctx):
        doc = read_json(ctx.paths.pages / f"{cid}.json")
        if not doc:
            continue
        found = candidate_lines(doc)
        candidates[cid] = found
        if found:
            with_lines += 1
            lines.append(f"### {cid} {doc['website']}")
            lines += [f"{i} [{c['key']}] {c['text']}" for i, c in enumerate(found, start=1)]
    folder = briefs_dir(ctx)
    (folder / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
    text = "\n".join(lines) + "\n"
    (folder / "brief.txt").write_text(text, encoding="utf-8")
    print(f"{len(candidates)} companies, {with_lines} with candidate lines, {len(text):,} characters")


def parse_decision(line: str) -> tuple[str, dict]:
    parts = line.split()
    picks = {}
    for token in parts[1:]:
        key, rest = token.split("=", 1)
        index, value = rest.split(":", 1)
        picks[key] = {"index": int(index), "value": value.rstrip("~"), "confidence": "medium" if value.endswith("~") else "high"}
    return parts[0], picks


def build_record(cid: str, doc: dict, rules: dict, found: list[dict], picks: dict) -> dict:
    record = {"company_id": cid, "units_managed": dict(NUL), "employees": dict(NUL), "founding_year": dict(NUL),
              "other_cities": dict(NUL), "company_summary": dict(NUL),
              "management_types": detect_management(doc), "digital_maturity": detect_digital(doc, rules)}
    for key, pick in picks.items():
        line = found[pick["index"] - 1]
        if line["key"] != key:
            raise ValueError(f"{cid}: line {pick['index']} is [{line['key']}], not [{key}]")
        value = pick["value"].split(",") if key == "c" else int(pick["value"])
        record[FIELD_BY_KEY[key]] = {"value": value, "source_url": line["url"], "evidence_quote": line["text"], "confidence": pick["confidence"]}
    return record


def apply_decisions(ctx) -> None:
    folder = briefs_dir(ctx)
    candidates = json.loads((folder / "candidates.json").read_text(encoding="utf-8"))
    decisions = {}
    path = folder / "decisions.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cid, picks = parse_decision(line)
                decisions[cid] = picks

    written = 0
    for cid, found in candidates.items():
        doc = read_json(ctx.paths.pages / f"{cid}.json")
        rules = read_json(ctx.paths.rules / f"{cid}.json") or {}
        record = build_record(cid, doc, rules, found, decisions.get(cid, {}))
        CompanyExtraction.model_validate(record)
        (ctx.paths.llm / f"{cid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
    print(f"{written} records written, {len(decisions)} with reviewed values")
