"""Stage 1, source A: OpenStreetMap via the Overpass API."""

import json
import logging
import time

import requests

log = logging.getLogger("leadengine")


def build_query(ctx) -> str:
    iso = ctx.city["osm_area_iso"]
    terms = ctx.keywords["osm_name_terms"]
    timeout = ctx.pipeline["overpass"]["timeout_seconds"]
    # WEG is matched case-sensitively and only on offices, so "Am Weg" cannot match.
    return f"""
[out:json][timeout:{timeout}];
area["ISO3166-2"="{iso}"]["admin_level"="4"]->.a;
(
  nwr["office"="property_management"](area.a);
  nwr["name"~"{terms}",i](area.a);
  nwr["office"]["name"~"(^|[^A-Za-z])WEG([^A-Za-z]|$)"](area.a);
);
out center tags;
""".strip()


def fetch(ctx, session=requests, sleep=time.sleep) -> list[dict]:
    """Return Overpass elements, from the cache when one exists."""
    cache = ctx.paths.raw_osm / f"{ctx.city['slug']}.json"
    if cache.exists():
        log.info("OSM: using cached response %s", cache)
        return json.loads(cache.read_text(encoding="utf-8"))["elements"]

    settings = ctx.pipeline["overpass"]
    headers = {"User-Agent": ctx.pipeline["http"]["user_agent"]}
    query = build_query(ctx)
    for attempt in range(4):
        ctx.ledger.record("overpass")
        resp = session.post(settings["url"], data={"data": query}, headers=headers,
                            timeout=settings["timeout_seconds"] + 30)
        if resp.status_code == 200:
            cache.write_bytes(resp.content)  # bytes, so no charset guessing can corrupt names
            return resp.json()["elements"]
        log.warning("OSM: HTTP %s, attempt %s", resp.status_code, attempt + 1)
        if resp.status_code in (429, 504):
            sleep(30 * (attempt + 1))
            continue
        resp.raise_for_status()
    raise RuntimeError("Overpass did not answer after 4 attempts.")


def to_row(element: dict) -> dict:
    tags = element.get("tags", {})
    center = element.get("center", {})
    street = " ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p)
    postcode = tags.get("addr:postcode")
    city = tags.get("addr:city")
    tail = " ".join(p for p in (postcode, city) if p)
    return {
        "source": "osm",
        "osm_id": f"{element['type']}/{element['id']}",
        "google_place_id": None,
        "company_name": tags.get("name"),
        "match_name": tags.get("name"),
        "website": tags.get("website") or tags.get("contact:website") or tags.get("url"),
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "osm_email": tags.get("email") or tags.get("contact:email"),
        "address": ", ".join(p for p in (street, tail) if p) or None,
        "postcode": postcode,
        "lat": element.get("lat", center.get("lat")),
        "lng": element.get("lon", center.get("lon")),
        "osm_office_tag": tags.get("office"),
    }


def collect(ctx) -> list[dict]:
    return [to_row(e) for e in fetch(ctx)]
