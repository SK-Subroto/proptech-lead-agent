"""Stage 4: combine everything into leads.csv and score each company."""

import re
from datetime import date

import pandas as pd

from leadengine.store import blank, read_candidates, read_json

LLM_FIELDS = ["units_managed", "management_types", "employees", "founding_year",
              "other_cities", "digital_maturity", "company_summary"]
RULE_FIELDS = ["legal_form", "handelsregister_no", "owner_portal", "remote_reading_signal",
               "hiring_signal", "software_mentioned", "metering_providers"]


def _from_bins(value, cfg_bins) -> tuple[float, str]:
    for b in cfg_bins:
        if b["below"] is None or value < b["below"]:
            return b["score"], b["label"].format(value=f"{value:,}")
    raise ValueError("bins must end with below: null")


def kpi_scores(k: dict, cfg: dict) -> dict:
    """Return {kpi: (s, label)} for known KPIs only; a missing KPI has no entry."""
    out = {}
    for name, key in [("units_managed", "units_managed"), ("employees", "employees"), ("years_in_business", "years_in_business")]:
        if not blank(k.get(key)):
            out[name] = _from_bins(int(k[key]), cfg["kpis"][name]["bins"])

    if k.get("management_types"):
        options = [cfg["management_type_scores"][t] for t in k["management_types"]]
        best = max(options, key=lambda o: o["score"])
        label = " and ".join(cfg["management_type_scores"][t]["label"] for t in k["management_types"])
        out["management_type"] = (best["score"], label)

    if k.get("digital_maturity"):
        o = cfg["digital_maturity_scores"][k["digital_maturity"]]
        out["digital_maturity"] = (o["score"], o["label"])

    # A keyword that was not found stays missing: absence is not evidence.
    if k.get("remote_reading_signal") == "mentioned":
        o = cfg["mentioned_scores"]["remote_reading_signal"]
        out["remote_reading_signal"] = (o["score"], o["label"])
    if k.get("hiring_signal") == "mentioned":
        o = cfg["mentioned_scores"]["hiring_signal"]
        out["hiring_signal"] = (o["score"], o["label"])
    if k.get("metering_providers"):
        o = cfg["mentioned_scores"]["metering_provider"]
        out["metering_provider"] = (o["score"], o["label"])

    if k.get("crawled"):
        status = k.get("email_status") if k.get("contact_name") else "no_contact"
        o = cfg["reachability_scores"][status]
        out["reachability"] = (o["score"], o["label"])

    if (k.get("berlin_locations") or 0) >= 2 or k.get("other_cities"):
        o = cfg["locations_scores"]["multiple"]
        out["locations"] = (o["score"], o["label"])
    return out


def score_company(k: dict, cfg: dict) -> dict:
    known = kpi_scores(k, cfg)
    neutral = cfg["neutral"]
    weights = {name: spec["weight"] for name, spec in cfg["kpis"].items()}
    total = sum(weights.values())
    weighted = sum(w * (known[name][0] if name in known else neutral) for name, w in weights.items())
    score = round(100 * weighted / total)
    completeness = round(100 * sum(weights[n] for n in known) / total, 1)
    tier = "A" if score >= cfg["tiers"]["A"] else "B" if score >= cfg["tiers"]["B"] else "C"

    ranked = sorted(known, key=lambda n: weights[n] * (known[n][0] - neutral), reverse=True)
    labels = [known[n][1] for n in ranked[:3]]
    reason = "; ".join(labels) if labels else "no KPI data found in public sources"
    if completeness < 40:
        reason += f"; limited data ({completeness:.0f}%)"
    cap = cfg.get("tier_caps", {}).get("self_managed_landlord")
    if k.get("self_managed_landlord") and cap and tier < cap:  # "A" < "B": better than the cap
        tier = cap
        reason += f"; capped at Tier {cap}: manages its own housing"
    return {"lead_score": score, "lead_tier": tier, "score_reason": reason, "data_completeness": completeness}


def contact_angle(row: dict, cfg: dict) -> str:
    def fmt(value):
        return ", ".join(value) if isinstance(value, list) else str(value)

    for angle in cfg["angles"]:
        when = angle["when"]
        if when == "default" \
                or (when == "remote_reading_signal" and row.get("remote_reading_signal") == "mentioned") \
                or (when == "metering_provider" and row.get("metering_providers")) \
                or (when == "digital_maturity_high" and row.get("digital_maturity") == "high" and row.get("software_mentioned")):
            return angle["text"].format(metering_providers=fmt(row.get("metering_providers")),
                                        software_mentioned=fmt(row.get("software_mentioned")))
    raise ValueError("angles must end with a default")


def _published(rule_field: dict | None, osm_value, osm_id):
    """Website value first, then OSM, with its source and confidence."""
    if rule_field and rule_field.get("value"):
        return rule_field["value"], rule_field.get("confidence"), rule_field.get("source_url")
    if not blank(osm_value) and not blank(osm_id):
        return osm_value, "medium", f"https://www.openstreetmap.org/{osm_id}"
    return None, None, None


def _load_exclusions(ctx) -> dict[str, str]:
    reasons = {}
    for name in ("excluded_after_crawl.csv", "excluded_after_geo.csv"):
        path = ctx.paths.processed / name
        if path.exists() and path.stat().st_size > 3:
            for row in pd.read_csv(path, encoding="utf-8-sig").to_dict("records"):
                reasons[row["company_id"]] = row["exclusion_reason"]
    return reasons


def build_row(company: dict, rules: dict | None, llm: dict | None, geo: dict, contact: dict, cfg: dict,
              self_managed_pattern: str = "$^") -> dict:
    rules, llm = rules or {}, llm or {}
    row = {
        "company_id": company["company_id"],
        "website": rules.get("website") or (company["website"] if not blank(company.get("website")) else None),
        "domain": company.get("domain") if not blank(company.get("domain")) else None,
        "sources": company["sources"],
        "google_place_id": company.get("google_place_id") if not blank(company.get("google_place_id")) else None,
        "collected_at": company["collected_at"],
    }
    osm_id = company.get("osm_id")
    for field_name, osm_value in [("company_name", company.get("company_name")), ("address", company.get("address")),
                                  ("postcode", company.get("postcode")), ("phone", company.get("phone"))]:
        value, conf, url = _published(rules.get(field_name), osm_value, osm_id)
        row[field_name], row[f"{field_name}_confidence"], row[f"{field_name}_source_url"] = value, conf, url

    # Google-only rows have no name at collect time, so check the published name too.
    row["self_managed_landlord"] = company.get("self_managed_landlord") in (True, "True") \
        or bool(re.search(self_managed_pattern, str(row["company_name"] or ""), re.I))

    for name in RULE_FIELDS:
        f = rules.get(name) or {}
        row[name], row[f"{name}_confidence"], row[f"{name}_source_url"] = f.get("value"), f.get("confidence"), f.get("source_url")
    for name in LLM_FIELDS:
        f = llm.get(name) or {}
        row[name], row[f"{name}_confidence"], row[f"{name}_source_url"] = f.get("value"), f.get("confidence"), f.get("source_url")

    locations = company.get("source_row_count") if osm_id and not blank(osm_id) else None
    row["berlin_locations"] = int(locations) if locations and locations >= 2 else None
    row["berlin_locations_confidence"] = "medium" if row["berlin_locations"] else None
    row["berlin_locations_source_url"] = f"https://www.openstreetmap.org/{osm_id}" if row["berlin_locations"] else None

    row["years_in_business"] = date.today().year - int(row["founding_year"]) if row["founding_year"] else None
    row["crawl_ok"] = bool(rules)
    row.update({k: geo.get(k) for k in ("lat", "lng", "geo_source", "geo_precision", "district", "in_city_boundary")})
    row.update({k: v for k, v in contact.items() if k != "company_id"})
    row["management_evidence"] = (rules.get("management_evidence") or {}).get("value")

    row.update(score_company(row, cfg))
    row["first_contact_angle"] = contact_angle(row, cfg)
    return row


def evidence_rows(cid: str, rules: dict, llm: dict) -> list[dict]:
    out = []
    for name, f in rules.items():
        if isinstance(f, dict) and f.get("value") not in (None, "not_mentioned") and "matched_text" in f:
            out.append({"company_id": cid, "field": name, "value": f["value"], "source_url": f.get("source_url"),
                        "evidence": f["matched_text"], "confidence": f.get("confidence")})
    for name in LLM_FIELDS:
        f = llm.get(name) or {}
        if f.get("value") is not None:
            out.append({"company_id": cid, "field": name, "value": f["value"], "source_url": f["source_url"],
                        "evidence": f["evidence_quote"], "confidence": f["confidence"]})
    return out


def run(ctx) -> dict:
    candidates = read_candidates(ctx)
    geo = pd.read_csv(ctx.paths.processed / "geo.csv", encoding="utf-8-sig").astype(object).where(lambda d: d.notna(), None)
    contacts = pd.read_csv(ctx.paths.processed / "contacts.csv", encoding="utf-8-sig").astype(object).where(lambda d: d.notna(), None)
    geo_by_id = {r["company_id"]: r for r in geo.to_dict("records")}
    contact_by_id = {r["company_id"]: r for r in contacts.to_dict("records")}
    exclusions = _load_exclusions(ctx)

    leads, excluded, evidence, dropped_google = [], [], [], 0
    for company in candidates.to_dict("records"):
        cid = company["company_id"]
        rules = read_json(ctx.paths.rules / f"{cid}.json")
        llm = read_json(ctx.paths.llm / f"{cid}.json")
        row = build_row(company, rules, llm, geo_by_id[cid], contact_by_id[cid], ctx.scoring,
                        ctx.keywords["self_managed_landlord"])
        google_only = company["sources"] == "google_discovery"
        if google_only and blank(row["company_name"]):
            dropped_google += 1  # nothing non-Google to publish
            continue
        if cid in exclusions:
            if google_only:
                dropped_google += 1
            else:
                excluded.append({"company_id": cid, "company_name": row["company_name"], "website": row["website"],
                                 "address": row["address"], "postcode": row["postcode"], "sources": row["sources"],
                                 "exclusion_reason": exclusions[cid]})
            continue
        leads.append(row)
        evidence += evidence_rows(cid, rules or {}, llm or {})

    df = pd.DataFrame(leads).sort_values(["lead_score", "data_completeness"], ascending=False)
    df.to_json(ctx.paths.processed / "leads.json", orient="records", force_ascii=False, indent=1)
    pd.DataFrame(evidence).to_csv(ctx.paths.processed / "evidence.csv", index=False, encoding="utf-8-sig")

    first_pass = ctx.paths.processed / "excluded.csv"
    earlier = pd.read_csv(first_pass, encoding="utf-8-sig") if first_pass.exists() and first_pass.stat().st_size > 3 else pd.DataFrame()
    pd.concat([earlier, pd.DataFrame(excluded)], ignore_index=True).to_csv(
        ctx.paths.processed / "excluded_final.csv", index=False, encoding="utf-8-sig")

    return {"leads": len(df), "excluded_after_crawl_or_geo": len(excluded), "google_only_not_listed": dropped_google,
            **{f"tier_{k}": int(v) for k, v in df["lead_tier"].value_counts().items()},
            "mean_completeness": round(float(df["data_completeness"].mean()), 1)}
