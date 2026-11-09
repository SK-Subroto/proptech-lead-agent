"""Stage 1: gather companies from all sources, merge, deduplicate, filter."""

import logging
import re

import pandas as pd
from rapidfuzz import fuzz

from leadengine.normalize import (
    company_id, is_platform_domain, normalize_domain, normalize_name, valid_postcode,
)
from leadengine.sources import google_places, osm

log = logging.getLogger("leadengine")

FUZZY_SAME_POSTCODE = 93
FUZZY_ONE_POSTCODE_MISSING = 96
# Names this short or generic cannot identify a company on their own.
GENERIC_NAMES = {"hausverwaltung", "immobilienverwaltung", "mietverwaltung", "verwaltung", "immobilien"}


class _Groups:
    """Union-find: rows joined by any rule end up as one company."""

    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(b)] = self.find(a)


def _usable_name(name: str) -> bool:
    return len(name) >= 8 and name not in GENERIC_NAMES


def find_duplicate_groups(rows: list[dict], platforms: list[str]) -> list[list[int]]:
    """Domain, then name + postcode, then fuzzy name. Returns lists of row indexes."""
    groups = _Groups(len(rows))
    domains = [normalize_domain(r.get("website")) for r in rows]
    names = [normalize_name(r.get("match_name")) for r in rows]
    postcodes = [str(r["postcode"]) if r.get("postcode") else "" for r in rows]

    first_by_domain: dict[str, int] = {}
    for i, domain in enumerate(domains):
        if domain and not is_platform_domain(domain, platforms):
            groups.union(first_by_domain.setdefault(domain, i), i)

    first_by_key: dict[tuple, int] = {}
    for i, (name, pc) in enumerate(zip(names, postcodes)):
        if _usable_name(name) and pc:
            groups.union(first_by_key.setdefault((name, pc), i), i)

    for i in range(len(rows)):
        if not _usable_name(names[i]):
            continue
        for j in range(i + 1, len(rows)):
            if not _usable_name(names[j]):
                continue
            # Two different own domains mean two companies, however similar the names.
            if domains[i] and domains[j] and domains[i] != domains[j] \
                    and not is_platform_domain(domains[i], platforms) \
                    and not is_platform_domain(domains[j], platforms):
                continue
            if postcodes[i] and postcodes[j]:
                if postcodes[i] != postcodes[j]:
                    continue
                threshold = FUZZY_SAME_POSTCODE
            else:
                threshold = FUZZY_ONE_POSTCODE_MISSING
            if fuzz.token_sort_ratio(names[i], names[j]) >= threshold:
                groups.union(i, j)

    merged: dict[int, list[int]] = {}
    for i in range(len(rows)):
        merged.setdefault(groups.find(i), []).append(i)
    return list(merged.values())


def _first(values):
    return next((v for v in values if v not in (None, "") and not pd.isna(v)), None)


def merge_group(rows: list[dict], platforms: list[str]) -> dict:
    # OSM rows first, so published values and coordinates are never Google's.
    rows = sorted(rows, key=lambda r: r["source"] != "osm")
    websites = [r.get("website") for r in rows if r.get("website")]
    own = [w for w in websites if not is_platform_domain(normalize_domain(w), platforms)]
    website = own[0] if own else None
    domain = normalize_domain(website)
    name = _first(r.get("company_name") for r in rows)
    postcode = _first(r.get("postcode") for r in rows)
    return {
        "company_id": company_id(domain, name or _first(r.get("match_name") for r in rows), postcode),
        "company_name": name,
        "match_name": _first(r.get("match_name") for r in rows),
        "website": website,
        "domain": domain,
        "platform_url": None if own else _first(websites),
        "phone": _first(r.get("phone") for r in rows),
        "osm_email": _first(r.get("osm_email") for r in rows),
        "address": _first(r.get("address") for r in rows),
        "postcode": postcode,
        "lat": _first(r.get("lat") for r in rows),
        "lng": _first(r.get("lng") for r in rows),
        "osm_office_tag": _first(r.get("osm_office_tag") for r in rows),
        "osm_id": _first(r.get("osm_id") for r in rows),
        "google_place_id": _first(r.get("google_place_id") for r in rows),
        "sources": ";".join(sorted({r["source"] for r in rows})),
        "source_row_count": len(rows),
    }


def non_target_reason(name: str | None, rules: list[dict]) -> str | None:
    """Return the first matching exclusion reason, or None for a valid target."""
    if not name:
        return None
    for rule in rules:
        if re.search(rule["exclude"], name, re.I) and not re.search(rule.get("unless", "$^"), name, re.I):
            return rule["reason"]
    return None


def classify(company: dict, ctx) -> str | None:
    name = company.get("match_name")
    if not name:
        return "no_name"
    if company.get("osm_office_tag") == "government":
        return "public_authority"
    reason = non_target_reason(name, ctx.keywords["non_target_rules"])
    if reason:
        return reason
    pc = ctx.city["postcode"]
    if company.get("postcode") and not valid_postcode(company["postcode"], pc["min"], pc["max"]):
        return "outside_postcode_range"
    return None


def would_lose_google_rows(path, google_rows: list, mode: str, limit) -> bool:
    """True when a rerun would silently overwrite Google-found companies with nothing."""
    if google_rows or mode == "off" or limit or not path.exists():
        return False
    return bool(pd.read_csv(path, encoding="utf-8-sig")["sources"].str.contains("google_discovery").any())


def run(ctx) -> dict:
    osm_rows = osm.collect(ctx)
    google_rows = google_places.collect(ctx)
    if would_lose_google_rows(ctx.paths.processed / "candidates.csv", google_rows,
                              ctx.pipeline["google"]["mode"], ctx.limit):
        raise SystemExit("Collect stopped: Google data is locked or purged, and this run would remove the "
                         "Google-found companies from candidates.csv. Keeping the existing file.")
    if not ctx.pipeline["vdiv"]["permission_confirmed"]:
        log.info("VDIV: disabled, written permission not confirmed")

    rows = osm_rows + google_rows
    platforms = ctx.keywords["platform_domains"]
    groups = find_duplicate_groups(rows, platforms)
    companies = [merge_group([rows[i] for i in g], platforms) for g in groups]

    kept, excluded, google_only_filtered = [], [], 0
    today = pd.Timestamp.today().date().isoformat()
    for company in companies:
        company["collected_at"] = today
        reason = classify(company, ctx)
        is_google_only = company["sources"] == "google_discovery"
        if reason:
            if is_google_only:
                google_only_filtered += 1  # Google names may not be listed
            else:
                excluded.append({**company, "exclusion_reason": reason})
            continue
        if is_google_only and not company["website"]:
            google_only_filtered += 1  # nothing to rebuild it from
            continue
        company["self_managed_landlord"] = bool(
            re.search(ctx.keywords["self_managed_landlord"], company["match_name"], re.I))
        kept.append(company)

    candidates = pd.DataFrame(kept)
    if ctx.limit:
        candidates = candidates.head(ctx.limit)
    # match_name may hold a Google name, so it is not written out for those rows.
    if not candidates.empty:
        candidates.loc[candidates["sources"] == "google_discovery", "match_name"] = None
    candidates.to_csv(ctx.paths.processed / "candidates.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(excluded).to_csv(ctx.paths.processed / "excluded.csv", index=False, encoding="utf-8-sig")

    return {
        "osm_rows": len(osm_rows),
        "google_rows": len(google_rows),
        "after_dedup": len(companies),
        "candidates": len(candidates),
        "with_website": int(candidates["website"].notna().sum()) if not candidates.empty else 0,
        "excluded_listed": len(excluded),
        "google_only_not_listed": google_only_filtered,
    }
