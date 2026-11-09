"""Stage 3c: locate companies and assign official districts.

Coordinates come only from OSM or Nominatim, never from Google (design section 4.2).
"""

import json
import logging
import time

import geopandas as gpd
import pandas as pd
import requests

from leadengine.store import blank, read_candidates, read_json

log = logging.getLogger("leadengine")

NOMINATIM_PRECISION = {
    "house": "exact", "building": "exact", "office": "exact", "amenity": "exact",
    "road": "street", "street": "street", "postcode": "postcode",
}


def boundaries_path(ctx):
    return ctx.paths.raw_geo / f"{ctx.city['slug']}_bezirke.geojson"


def load_boundaries(ctx, session=requests) -> gpd.GeoDataFrame:
    """Return polygons in WGS84 with a `district` column, downloading once."""
    cfg = ctx.city["boundary"]
    path = boundaries_path(ctx)
    if not path.exists():
        ctx.ledger.record("geoportal_wfs")
        resp = session.get(cfg["url"], params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": cfg["layer"], "outputFormat": "application/json",
        }, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    districts = gpd.read_file(path).set_crs(cfg["source_crs"], allow_override=True).to_crs(4326)
    districts = districts.rename(columns={cfg["name_field"]: "district"})[["district", "geometry"]]
    if len(districts) != cfg["expected_count"]:
        raise RuntimeError(f"Expected {cfg['expected_count']} districts, got {len(districts)}")
    return districts


class Geocoder:
    """One thread, at most one request per interval, every answer cached."""

    def __init__(self, ctx, session=requests, sleep=time.sleep, clock=time.monotonic):
        self.ctx = ctx
        self.session = session
        self.sleep = sleep
        self.clock = clock
        self.path = ctx.paths.raw_geo / "nominatim_cache.json"
        self.cache = read_json(self.path) or {}
        self.last_call = None
        self.settings = ctx.pipeline["nominatim"]

    def locate(self, street: str, postcode: str, city: str) -> dict | None:
        key = f"{street}|{postcode}|{city}"
        if key in self.cache:
            return self.cache[key]
        if self.last_call is not None:
            wait = self.settings["min_seconds_between_requests"] - (self.clock() - self.last_call)
            if wait > 0:
                self.sleep(wait)
        self.last_call = self.clock()
        self.ctx.ledger.record("nominatim")
        resp = self.session.get(self.settings["url"], params={
            "street": street, "postalcode": postcode, "city": city,
            "countrycodes": "de", "format": "jsonv2", "limit": 1,
        }, headers={"User-Agent": self.ctx.pipeline["http"]["user_agent"]}, timeout=30)
        resp.raise_for_status()
        hits = resp.json()
        result = None
        if hits:
            hit = hits[0]
            result = {"lat": float(hit["lat"]), "lng": float(hit["lon"]),
                      "precision": NOMINATIM_PRECISION.get(hit.get("addresstype"), "street")}
        self.cache[key] = result
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        return result


def locate_company(company: dict, rules: dict | None, geocoder) -> dict:
    if not blank(company.get("lat")) and not blank(company.get("osm_id")):
        return {"lat": company["lat"], "lng": company["lng"], "geo_source": "osm", "geo_precision": "exact"}
    if rules and rules.get("address_street") and rules["postcode"]["value"]:
        hit = geocoder.locate(rules["address_street"], rules["postcode"]["value"], rules.get("address_city") or "")
        if hit:
            return {"lat": hit["lat"], "lng": hit["lng"], "geo_source": "nominatim", "geo_precision": hit["precision"]}
    return {"lat": None, "lng": None, "geo_source": "none", "geo_precision": "none"}


def assign_districts(located: pd.DataFrame, districts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Add `district` and `in_city_boundary`. Postcode-level hits are too coarse to assign."""
    out = located.copy()
    out["district"] = None
    out["in_city_boundary"] = None
    has_point = out["lat"].notna() & out["lng"].notna()
    if not has_point.any():
        return out
    points = gpd.GeoDataFrame(out[has_point], geometry=gpd.points_from_xy(out.loc[has_point, "lng"], out.loc[has_point, "lat"]), crs=4326)
    joined = gpd.sjoin(points, districts, how="left", predicate="within", rsuffix="poly")
    joined = joined[~joined.index.duplicated()]
    out.loc[joined.index, "in_city_boundary"] = joined["district_poly"].notna()
    assignable = joined[joined["geo_precision"] != "postcode"]
    out.loc[assignable.index, "district"] = assignable["district_poly"]
    return out


def run(ctx) -> dict:
    candidates = read_candidates(ctx)
    districts = load_boundaries(ctx)
    geocoder = Geocoder(ctx)
    rows = []
    for company in candidates.to_dict("records"):
        rules = read_json(ctx.paths.rules / f"{company['company_id']}.json")
        rows.append({"company_id": company["company_id"], **locate_company(company, rules, geocoder)})

    located = pd.DataFrame(rows)
    located["lat"] = pd.to_numeric(located["lat"], errors="coerce")
    located["lng"] = pd.to_numeric(located["lng"], errors="coerce")
    result = assign_districts(located, districts)
    result.to_csv(ctx.paths.processed / "geo.csv", index=False, encoding="utf-8-sig")

    outside = result[result["in_city_boundary"] == False]  # noqa: E712
    outside.assign(exclusion_reason="outside_city_boundary")[["company_id", "exclusion_reason"]].to_csv(
        ctx.paths.processed / "excluded_after_geo.csv", index=False, encoding="utf-8-sig")
    return {
        "located": int(result["lat"].notna().sum()),
        "unlocated": int(result["lat"].isna().sum()),
        "outside_boundary": len(outside),
        "with_district": int(result["district"].notna().sum()),
        "nominatim_requests_this_month": ctx.ledger.count("nominatim"),
    }
