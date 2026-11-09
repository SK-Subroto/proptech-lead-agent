"""Stage 7: write the workbook, one CSV per tab, and the run summary."""

import json
from datetime import date

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from leadengine.stages.validate import load_leads
from leadengine.store import blank, read_json

NOT_STATED = "not stated"
NOT_MENTIONED = "not mentioned"
HEADER_FILL = "1C5CAB"
TIER_FILLS = {"A": "CDE2FB", "B": "FBE0D3", "C": "D1F0E4"}

SIGNAL_FIELDS = ["owner_portal", "remote_reading_signal", "hiring_signal"]
KPI_VALUE_COLUMNS = [
    "units_managed", "management_types", "employees", "founding_year", "years_in_business",
    "berlin_locations", "other_cities", "digital_maturity", "owner_portal", "software_mentioned",
    "metering_providers", "remote_reading_signal", "hiring_signal", "company_summary",
    "contact_name", "contact_role", "contact_email",
]
TRIPLES = ["company_name", "address", "postcode", "phone", "legal_form", "handelsregister_no",
           "units_managed", "management_types", "employees", "founding_year", "berlin_locations",
           "other_cities", "digital_maturity", "owner_portal", "software_mentioned", "metering_providers",
           "remote_reading_signal", "hiring_signal", "company_summary", "contact_name", "contact_email"]
LEADS_ORDER = [
    "lead_score", "lead_tier", "score_reason", "data_completeness", "first_contact_angle",
    "company_name", "legal_form", "website", "address", "postcode", "district", "phone",
    "units_managed", "management_types", "employees", "founding_year", "years_in_business",
    "berlin_locations", "other_cities", "digital_maturity", "owner_portal", "software_mentioned",
    "metering_providers", "remote_reading_signal", "hiring_signal", "company_summary",
    "contact_name", "contact_role", "contact_email", "email_status", "generic_email",
    "linkedin_search_url", "linkedin_profile_url", "xing_search_url",
    "handelsregister_no", "domain", "lat", "lng", "geo_source", "geo_precision", "in_city_boundary",
    "sources", "google_place_id", "website_reachable", "qa_flags", "collected_at", "company_id",
]
TOP_COLUMNS = ["lead_score", "company_name", "score_reason", "first_contact_angle", "contact_name",
               "contact_role", "contact_email", "email_status", "phone", "website", "district", "linkedin_search_url"]

KPI_DEFINITIONS = [
    ("Units managed", "units_managed", "Residential or commercial units the company states it manages.",
     "Main driver of meter count and revenue. Very large groups often buy centrally, so the top band scores slightly lower.", "Company website, read by Claude Code"),
    ("Management type", "management_type", "WEG, rental (Miet), SE or commercial management.",
     "WEG and rental management need heating and water cost billing, which is Heidi's service.", "Company website, read by Claude Code"),
    ("Employees", "employees", "Stated team size.", "Proxy for portfolio size and for how complex the buying process is.", "Company website, read by Claude Code"),
    ("Remote reading signal", "remote_reading_signal", "Site mentions Funk, fernablesbar or HeizkostenV.",
     "§ 5 Abs. 3 HeizkostenV requires non-remote-readable devices to be retrofitted by 31.12.2026, with exemptions. A mention shows the topic is live; it does not prove the company still needs to convert.", "Rule-based keyword match"),
    ("Metering provider named", "metering_provider", "Site names ista, Techem, Brunata, Minol, Kalo or similar.",
     "Shows metering is bought externally and who the competitor is.", "Rule-based keyword match"),
    ("Digital maturity", "digital_maturity", "High, medium or low, from stated portals, apps and software.",
     "Digital firms tend to adopt a new provider faster.", "Company website, read by Claude Code"),
    ("Years in business", "years_in_business", "Current year minus stated founding year.",
     "Stability. Long-running firms often hold legacy metering contracts.", "Company website, read by Claude Code"),
    ("Reachability", "reachability", "Named decision-maker and the email status.", "Sales can only act on a lead it can reach.", "Impressum and team pages"),
    ("Hiring signal", "hiring_signal", "Site advertises open positions.", "Growth, and a sign of changing processes.", "Rule-based keyword match"),
    ("Locations and other cities", "locations", "More than one Berlin location, or other cities.", "Scale and expansion.", "OSM and company website"),
    ("Data completeness", None, "Share of the scoring weight based on real data.", "Shows how much to trust a score. Missing KPIs use a neutral value, not zero.", "Calculated"),
    ("Lead score, tier, reason", None, "0-100; A at 70 or above, B 40-69, C below 40.", "Final ranking. Weights are reasoned judgement, not calibrated on win/loss data.", "config/scoring.yaml"),
]


def _cell(value):
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    if blank(value):
        return None
    if isinstance(value, str) and value[:1] == "=":
        return "'" + value  # crawled text must never become a spreadsheet formula
    return value


def display_value(column: str, value):
    """R9.5: an unknown KPI reads 'not stated', never a blank or an estimate."""
    if value == "not_mentioned":
        return NOT_MENTIONED
    if column in KPI_VALUE_COLUMNS and (blank(value) or (isinstance(value, list) and not value)):
        return NOT_MENTIONED if column in SIGNAL_FIELDS else NOT_STATED
    return value


def leads_frame(df: pd.DataFrame) -> pd.DataFrame:
    columns = []
    for name in LEADS_ORDER:
        if name in df.columns:
            columns.append(name)
            if name in TRIPLES:
                columns += [f"{name}{suffix}" for suffix in ("_confidence", "_source_url") if f"{name}{suffix}" in df.columns]
    out = df[columns].copy().astype(object)
    for column in KPI_VALUE_COLUMNS:
        if column in out.columns:
            out[column] = [display_value(column, v) for v in df[column]]
    return out.sort_values(["lead_score", "data_completeness"], ascending=False)


def fill_rates(df: pd.DataFrame) -> list[tuple[str, float]]:
    rates = []
    for column in KPI_VALUE_COLUMNS:
        if column in df.columns:
            known = df[column].map(lambda v: not (blank(v) or v == "not_mentioned" or (isinstance(v, list) and not v)))
            rates.append((column, round(100 * float(known.mean()), 1)))
    return rates


def _write_sheet(wb: Workbook, title: str, frame: pd.DataFrame, first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(list(frame.columns))
    for row in frame.itertuples(index=False):
        ws.append([_cell(v) for v in row])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, column in enumerate(frame.columns, start=1):
        sample = [len(str(v)) for v in frame[column].head(200) if not blank(v)]
        width = min(max([len(str(column)) + 2] + sample), 48)
        ws.column_dimensions[get_column_letter(i)].width = width
    return ws


def _color_tiers(ws, column_name: str) -> None:
    index = next((c.column for c in ws[1] if c.value == column_name), None)
    if index is None:
        return
    letter = get_column_letter(index)
    target = f"{letter}2:{letter}{max(ws.max_row, 2)}"
    for tier, color in TIER_FILLS.items():
        ws.conditional_formatting.add(target, CellIsRule(operator="equal", formula=[f'"{tier}"'],
                                                         fill=PatternFill("solid", bgColor=color, fgColor=color)))


def kpi_frame(ctx) -> pd.DataFrame:
    rows = []
    for label, key, definition, why, source in KPI_DEFINITIONS:
        weight = ctx.scoring["kpis"][key]["weight"] if key else "n/a"
        rows.append({"KPI": label, "Definition": definition, "Why it matters to Heidi": why, "Data source": source, "Weight": weight})
    return pd.DataFrame(rows)


def _stage_counts(ctx) -> dict:
    return read_json(ctx.paths.processed / "stage_counts.json") or {}


def method_frame(ctx, df: pd.DataFrame) -> pd.DataFrame:
    rows = [("Run date", date.today().isoformat()), ("City", ctx.city["name"]), ("Cost", "EUR 0"),
            ("Google mode", ctx.pipeline["google"]["mode"]),
            ("Google key present", "yes" if ctx.google_api_key else "no: source skipped")]
    rows += [
        ("Source", "OpenStreetMap via Overpass. Data © OpenStreetMap contributors, ODbL 1.0. OSM-derived fields in this file are under ODbL."),
        ("Source", "Company websites: Impressum, home and up to 3 relevant pages. robots.txt respected."),
        ("Source", ctx.city["boundary"]["attribution"]),
        ("Source", "Nominatim for addresses without OSM coordinates. Data © OpenStreetMap contributors, ODbL 1.0."),
        ("Source not used", "VDIV Berlin-Brandenburg: its Impressum limits copies to private, non-commercial use, and no written permission was obtained."),
        ("Tools", "Python, requests, BeautifulSoup, trafilatura, pandas, geopandas, folium, openpyxl. KPI reading by Claude Code, no paid API."),
        ("Legal note", "Only publicly listed business contact data. In Germany cold email is restricted by UWG §7: first contact by phone or another compliant channel."),
        ("Legal note", "pattern_inferred emails are built from an address pattern seen on the same domain. They are unverified."),
    ]
    rows += [(f"Stage: {stage}", json.dumps(info["counts"], ensure_ascii=False)) for stage, info in _stage_counts(ctx).items()]
    rows += [(f"Requests {month}: {service}", count) for month, services in ctx.ledger.summary().items() for service, count in services.items()]
    rows += [(f"Fill rate: {column}", f"{rate}%") for column, rate in fill_rates(df)]
    rows.append(("Share with named contact", f"{round(100 * df['contact_name'].notna().mean(), 1)}%"))
    rows.append(("Share with personal email on site", f"{round(100 * (df['email_status'] == 'found_on_site').mean(), 1)}%"))
    return pd.DataFrame(rows, columns=["Item", "Value"])


def validation_frame(ctx) -> pd.DataFrame:
    checks = [("Manual check by a person", "validation_results.json"),
              ("AI re-check, before fixes (not independent)", "validation_ai_check.json")]
    rows = []
    for label, filename in checks:
        results = read_json(ctx.paths.processed / filename)
        if not results:
            continue
        rows += [{"Check": label, "Field": name, "Correct": v["correct"], "Wrong": v["wrong"],
                  "Not verifiable": v["not verifiable"], "Accuracy %": v["accuracy"]}
                 for name, v in results["fields"].items()]
        totals = results["totals"]
        rows.append({"Check": label, "Field": "OVERALL", "Correct": totals["correct"], "Wrong": totals["wrong"],
                     "Not verifiable": totals["not verifiable"], "Accuracy %": results["overall"]})
    if not rows:
        rows = [{"Check": "Pending", "Field": "Manual sample pending", "Correct": None, "Wrong": None, "Not verifiable": None, "Accuracy %": None}]
    return pd.DataFrame(rows)


def excluded_frame(ctx) -> pd.DataFrame:
    path = ctx.paths.processed / "excluded_final.csv"
    if not path.exists() or path.stat().st_size <= 3:
        return pd.DataFrame(columns=["company_name", "website", "address", "postcode", "sources", "exclusion_reason"])
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["company_name"] = df["company_name"].fillna(df.get("match_name"))
    return df[["company_name", "website", "address", "postcode", "sources", "exclusion_reason"]]


def run(ctx) -> dict:
    df = load_leads(ctx)
    leads = leads_frame(df)
    top = df[df["lead_tier"] == "A"].sort_values("lead_score", ascending=False).head(25)
    top = top[TOP_COLUMNS].astype(object)
    for column in ("contact_name", "contact_role", "contact_email"):
        top[column] = [display_value(column, v) for v in top[column]]

    tabs = [("Leads", leads), ("Top 25", top), ("KPI Definitions", kpi_frame(ctx)),
            ("Method & Sources", method_frame(ctx, df)), ("Validation", validation_frame(ctx)), ("Excluded", excluded_frame(ctx))]

    wb = Workbook()
    for i, (title, frame) in enumerate(tabs):
        ws = _write_sheet(wb, title, frame, first=i == 0)
        if title == "Leads":
            _color_tiers(ws, "lead_tier")
    basename = ctx.city["output_basename"]
    wb.save(ctx.paths.outputs / f"{basename}.xlsx")

    for title, frame in tabs:
        slug = title.lower().replace(" & ", "_").replace(" ", "_")
        frame.to_csv(ctx.paths.outputs / f"{basename}_{slug}.csv", index=False, encoding="utf-8-sig")

    write_run_summary(ctx)
    return {"leads_rows": len(leads), "top_rows": len(top), "excluded_rows": len(tabs[-1][1])}


def write_run_summary(ctx) -> None:
    stages = _stage_counts(ctx)
    lines = ["# Run summary", "", f"Date: {date.today().isoformat()}  ", f"City: {ctx.city['name']}  ", "Cost: EUR 0", "",
             "## Stages", "", "| Stage | Seconds | Counts |", "|---|---:|---|"]
    for stage, info in stages.items():
        lines.append(f"| {stage} | {info['seconds']} | {json.dumps(info['counts'], ensure_ascii=False)} |")
    lines += ["", f"Total recorded stage time: {round(sum(i['seconds'] for i in stages.values()), 1)} seconds.", "",
              "## External requests", "", "| Month | Service | Requests |", "|---|---|---:|"]
    for month, services in ctx.ledger.summary().items():
        for service, count in services.items():
            lines.append(f"| {month} | {service} | {count} |")
    lines += ["", "Stage 3b ran in Claude Code on the owner's Pro plan: no API charge."]
    (ctx.paths.outputs / "run_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
