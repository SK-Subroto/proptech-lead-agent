"""
Step 1 - Collect property management companies (Hausverwaltungen) in Berlin
using the Google Places API (New) Text Search.

How it works
- Berlin's bounding box is split into a 4x4 grid of tiles.
- Several German search terms are run in every tile.
- The API returns at most 60 results per search. If a tile hits that limit,
  it is split into 4 smaller tiles and searched again (adaptive tiling).
- Only operational businesses located in Berlin are kept.
- Results are deduplicated by Google place ID, then by website domain.
- Names that do not look like a property manager are flagged for review,
  not deleted.

Usage
    pip install requests pandas
    export GOOGLE_MAPS_API_KEY="your-key"        (Windows: set GOOGLE_MAPS_API_KEY=your-key)
    python collect_hausverwaltungen.py

Output
    hausverwaltungen_berlin_raw.csv   -> clean list, one row per company
    places_raw.json                   -> full API responses, kept for traceability
"""

import json
import os
import re
import sys
import time
from datetime import date
from urllib.parse import urlparse

import pandas as pd
import requests

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
URL = "https://places.googleapis.com/v1/places:searchText"

QUERIES = [
    "Hausverwaltung",
    "Immobilienverwaltung",
    "WEG Verwaltung",
    "Mietverwaltung",
]

# Approximate bounding box of Berlin
BERLIN = {"south": 52.338, "west": 13.088, "north": 52.676, "east": 13.761}

START_GRID = 4   # start with 4 x 4 tiles
MAX_DEPTH = 2    # a full tile may be split at most twice
MAX_PAGES = 3    # 20 results per page, 60 per search

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.location",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.businessStatus",
    "places.types",
    "places.rating",
    "places.userRatingCount",
    "nextPageToken",
])

RELEVANT_NAME = re.compile(
    r"verwalt|hausverw|immobilien|property|management|weg|real estate|grundbesitz|wohnungs",
    re.IGNORECASE,
)

request_count = 0


def search(query, rect):
    """Run one text search inside a rectangle, following up to 3 result pages."""
    global request_count
    body = {
        "textQuery": query,
        "languageCode": "de",
        "regionCode": "DE",
        "pageSize": 20,
        "locationRestriction": {
            "rectangle": {
                "low": {"latitude": rect["south"], "longitude": rect["west"]},
                "high": {"latitude": rect["north"], "longitude": rect["east"]},
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }

    places = []
    for _ in range(MAX_PAGES):
        for attempt in range(3):
            resp = requests.post(URL, headers=headers, json=body, timeout=30)
            request_count += 1
            if resp.status_code == 200:
                break
            print(f"  ! HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code in (429, 500, 503):
                time.sleep(2 ** attempt)
                continue
            return places
        else:
            return places

        data = resp.json()
        places.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
        time.sleep(1)
    return places


def grid(rect, n):
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


def crawl(query, rect, depth, results):
    places = search(query, rect)
    for p in places:
        entry = results.setdefault(p["id"], {"place": p, "queries": set()})
        entry["queries"].add(query)
    if len(places) >= 60 and depth < MAX_DEPTH:
        for sub in grid(rect, 2):
            crawl(query, sub, depth + 1, results)


def component(place, kind):
    for c in place.get("addressComponents", []):
        if kind in c.get("types", []):
            return c.get("longText")
    return None


def domain_of(url):
    if not url:
        return None
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def in_berlin(place):
    if component(place, "locality") == "Berlin":
        return True
    pc = component(place, "postal_code") or ""
    return pc.isdigit() and 10115 <= int(pc) <= 14199


def main():
    if not API_KEY:
        sys.exit("Please set the GOOGLE_MAPS_API_KEY environment variable.")

    results = {}
    tiles = list(grid(BERLIN, START_GRID))
    for q in QUERIES:
        print(f"Searching '{q}' in {len(tiles)} tiles ...")
        for t in tiles:
            crawl(q, t, 0, results)
        print(f"  unique places so far: {len(results)} | API requests: {request_count}")

    with open("places_raw.json", "w", encoding="utf-8") as f:
        json.dump(
            [{"queries": sorted(v["queries"]), **v["place"]} for v in results.values()],
            f, ensure_ascii=False, indent=1,
        )

    rows = []
    for v in results.values():
        p = v["place"]
        if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
            continue
        if not in_berlin(p):
            continue
        name = p.get("displayName", {}).get("text", "")
        loc = p.get("location", {})
        rows.append({
            "place_id": p["id"],
            "name": name,
            "address": p.get("formattedAddress"),
            "postal_code": component(p, "postal_code"),
            "district_google": component(p, "sublocality_level_1"),
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
            "website": p.get("websiteUri"),
            "domain": domain_of(p.get("websiteUri")),
            "phone": p.get("nationalPhoneNumber"),
            "google_rating": p.get("rating"),
            "google_reviews": p.get("userRatingCount", 0),
            "types": ";".join(p.get("types", [])),
            "matched_queries": ";".join(sorted(v["queries"])),
            "name_looks_relevant": bool(RELEVANT_NAME.search(name)),
            "source": "Google Places API (New)",
            "collected_at": date.today().isoformat(),
        })

    df = pd.DataFrame(rows)
    print(f"Operational places in Berlin: {len(df)}")

    # Deduplicate by website domain: keep the location with most reviews,
    # and record how many Berlin locations the company has.
    with_domain = df[df["domain"].notna()].copy()
    without_domain = df[df["domain"].isna()].copy()
    counts = with_domain.groupby("domain")["place_id"].transform("count")
    with_domain["locations_count"] = counts
    with_domain = (
        with_domain.sort_values("google_reviews", ascending=False)
        .drop_duplicates("domain", keep="first")
    )
    without_domain["locations_count"] = 1

    final = pd.concat([with_domain, without_domain], ignore_index=True)
    final = final.sort_values(["name_looks_relevant", "google_reviews"], ascending=[False, False])
    final.to_csv("hausverwaltungen_berlin_raw.csv", index=False, encoding="utf-8-sig")

    print(f"Companies after dedup: {len(final)}")
    print(f"  with website: {final['domain'].notna().sum()}")
    print(f"  flagged for review (name not obviously relevant): {(~final['name_looks_relevant']).sum()}")
    print(f"Total API requests: {request_count}")
    print("Saved: hausverwaltungen_berlin_raw.csv, places_raw.json")


if __name__ == "__main__":
    main()
