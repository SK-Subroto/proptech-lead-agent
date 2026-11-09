"""Stage 1, source B: Google Places API (New) Text Search.

Refactored from collect_hausverwaltungen.py. The adaptive tiling is unchanged:
a tile that returns the 60-result maximum is split in 4 and searched again.

Added: a monthly request budget, centre-outward tile order, and discovery_only
output. See specs/design.md section 4 for why.
"""

import json
import logging
import math
import shutil
import time

import requests

from leadengine.ledger import BudgetExceeded
from leadengine.normalize import valid_postcode

log = logging.getLogger("leadengine")

URL = "https://places.googleapis.com/v1/places:searchText"
LEDGER_KEY = "google_text_search_enterprise"
RAW_FILE = "places_raw.json"

# websiteUri makes this the Enterprise SKU: 1,000 free calls per month.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.addressComponents",
    "places.websiteUri",
    "places.businessStatus",
    "nextPageToken",
])


def grid(rect: dict, n: int):
    dlat = (rect["north"] - rect["south"]) / n
    dlng = (rect["east"] - rect["west"]) / n
    for i in range(n):
        for j in range(n):
            yield {
                "south": rect["south"] + i * dlat,
                "north": rect["south"] + (i + 1) * dlat,
                "west": rect["west"] + j * dlng,
                "east": rect["west"] + (j + 1) * dlng,
            }


def centre_first(tiles: list[dict], center: dict) -> list[dict]:
    """Dense central tiles first, so a budget stop only loses the sparse edge."""
    def distance(t):
        return math.hypot((t["south"] + t["north"]) / 2 - center["lat"],
                          (t["west"] + t["east"]) / 2 - center["lng"])
    return sorted(tiles, key=distance)


def worst_case_requests(cfg: dict) -> int:
    searches_per_tile = sum(4 ** d for d in range(cfg["max_depth"] + 1))
    return len(cfg["queries"]) * cfg["start_grid"] ** 2 * searches_per_tile * cfg["max_pages"]


class Searcher:
    def __init__(self, ctx, session=requests, sleep=time.sleep, max_pages=None):
        self.ctx = ctx
        self.session = session
        self.sleep = sleep
        self.budget = ctx.pipeline["google"]["monthly_request_budget"]
        self.max_pages = max_pages or ctx.city["google"]["max_pages"]
        self.max_depth = ctx.city["google"]["max_depth"]

    def _post(self, body: dict):
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.ctx.google_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        for attempt in range(3):
            self.ctx.ledger.record(LEDGER_KEY, budget=self.budget)
            resp = self.session.post(URL, headers=headers, json=body, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            log.warning("Google: HTTP %s", resp.status_code)
            if resp.status_code in (429, 500, 503):
                self.sleep(2 ** attempt)
                continue
            return None
        return None

    def search(self, query: str, rect: dict) -> list[dict]:
        body = {
            "textQuery": query,
            "languageCode": "de",
            "regionCode": "DE",
            "pageSize": 20,
            "locationRestriction": {"rectangle": {
                "low": {"latitude": rect["south"], "longitude": rect["west"]},
                "high": {"latitude": rect["north"], "longitude": rect["east"]},
            }},
        }
        places = []
        for _ in range(self.max_pages):
            data = self._post(body)
            if data is None:
                break
            places.extend(data.get("places", []))
            token = data.get("nextPageToken")
            if not token:
                break
            body["pageToken"] = token
            self.sleep(1)
        return places

    def crawl(self, query: str, rect: dict, depth: int, results: dict) -> None:
        places = self.search(query, rect)
        for p in places:
            results.setdefault(p["id"], p)
        if len(places) >= 60 and depth < self.max_depth:
            for sub in grid(rect, 2):
                self.crawl(query, sub, depth + 1, results)


def component(place: dict, kind: str):
    for c in place.get("addressComponents", []):
        if kind in c.get("types", []):
            return c.get("longText")
    return None


def in_city(place: dict, ctx) -> bool:
    if component(place, "locality") == ctx.city["name"]:
        return True
    pc = ctx.city["postcode"]
    return valid_postcode(component(place, "postal_code"), pc["min"], pc["max"])


def to_row(place: dict) -> dict:
    """discovery_only: nothing here is published except the place ID and website."""
    return {
        "source": "google_discovery",
        "osm_id": None,
        "google_place_id": place["id"],
        "company_name": None,
        "match_name": place.get("displayName", {}).get("text"),
        "website": place.get("websiteUri"),
        "phone": None,
        "osm_email": None,
        "address": None,
        "postcode": None,
        "lat": None,
        "lng": None,
        "osm_office_tag": None,
    }


def discover(ctx, session=requests, sleep=time.sleep) -> dict:
    """Return {place_id: place}, reusing stored responses so a rerun costs nothing."""
    raw_path = ctx.paths.raw_google / RAW_FILE
    if raw_path.exists():
        log.info("Google: using stored responses, no requests sent")
        return json.loads(raw_path.read_text(encoding="utf-8"))

    cfg = ctx.city["google"]
    dry_run = ctx.limit is not None
    tiles = centre_first(list(grid(ctx.city["bbox"], cfg["start_grid"])), ctx.city["center"])
    queries = cfg["queries"]
    if dry_run:
        tiles, queries = tiles[:1], queries[:1]

    searcher = Searcher(ctx, session, sleep, max_pages=1 if dry_run else None)
    if dry_run:
        searcher.max_depth = 0

    results = {}
    try:
        for query in queries:
            for tile in tiles:
                searcher.crawl(query, tile, 0, results)
            log.info("Google: '%s' done, %s unique places, %s requests this month",
                     query, len(results), ctx.ledger.count(LEDGER_KEY))
    except BudgetExceeded as exc:
        log.warning("Google: stopped at budget (%s). Keeping results so far.", exc)

    if not dry_run:
        raw_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    return results


def collect(ctx, session=requests, sleep=time.sleep) -> list[dict]:
    mode = ctx.pipeline["google"]["mode"]
    if mode == "off":
        log.info("Google: mode is off, skipping")
        return []
    if not ctx.google_api_key:
        log.info("Google: no GOOGLE_MAPS_API_KEY, skipping")
        return []
    if mode != "discovery_only":
        raise SystemExit(f"Unknown google.mode '{mode}'")

    stored = (ctx.paths.raw_google / RAW_FILE).exists()
    if ctx.limit is None and not stored and not ctx.pipeline["google"].get("full_run_confirmed"):
        log.warning("Google: full run is locked (google.full_run_confirmed is false). No requests sent.")
        return []

    log.info("Google: worst case %s requests, budget %s, used this month %s",
             worst_case_requests(ctx.city["google"]),
             ctx.pipeline["google"]["monthly_request_budget"],
             ctx.ledger.count(LEDGER_KEY))
    places = discover(ctx, session, sleep)
    rows = []
    for place in places.values():
        if place.get("businessStatus", "OPERATIONAL") == "OPERATIONAL" and in_city(place, ctx):
            rows.append(to_row(place))
    return rows


def purge(ctx) -> None:
    """Delete stored Google responses. Place IDs in the candidate list remain."""
    shutil.rmtree(ctx.paths.raw_google, ignore_errors=True)
    ctx.paths.raw_google.mkdir(parents=True, exist_ok=True)
    log.info("Google: stored responses deleted")
