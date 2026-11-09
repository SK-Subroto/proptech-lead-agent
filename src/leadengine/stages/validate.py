"""Stage 6: automatic checks, the manual sample, and the accuracy report."""

import json
import re
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from leadengine.normalize import normalize_domain, normalize_name, valid_postcode
from leadengine.store import blank, read_json, write_json

URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.I)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
SAMPLE_FIELDS = ["company_name", "address", "postcode", "website", "contact_name",
                 "contact_email", "units_managed", "management_types"]
CHOICES = ("correct", "wrong", "not verifiable")
SAMPLE_FILE = "validation_sample.xlsx"


def leads_path(ctx) -> Path:
    return ctx.paths.processed / "leads.json"


def load_leads(ctx) -> pd.DataFrame:
    return pd.read_json(leads_path(ctx), orient="records", dtype={"postcode": str, "company_id": str})


def row_flags(row: dict, ctx, crawl_status: str | None) -> list[str]:
    flags = []
    pc = ctx.city["postcode"]
    if not blank(row.get("postcode")) and not valid_postcode(row["postcode"], pc["min"], pc["max"]):
        flags.append("postcode_outside_city")
    if not blank(row.get("website")) and not URL_RE.match(str(row["website"])):
        flags.append("invalid_url")
    for key in ("contact_email", "generic_email"):
        if not blank(row.get(key)) and not EMAIL_RE.match(str(row[key])):
            flags.append(f"invalid_{key}")
    if crawl_status and crawl_status not in ("ok", "no_website"):
        flags.append(f"website_{crawl_status}")
    if row.get("self_managed_landlord") is True:
        flags.append("self_managed_landlord")
    if row.get("management_evidence") == "not_mentioned":
        flags.append("management_unconfirmed")
    if row.get("email_status") == "pattern_inferred":
        flags.append("email_unverified_pattern")
    if row.get("company_name_confidence") == "low":
        flags.append("name_needs_review")
    return flags


def add_duplicate_flags(df: pd.DataFrame) -> pd.Series:
    domains = df["domain"].map(lambda d: None if blank(d) else normalize_domain(d))
    names = df["company_name"].map(normalize_name) + "|" + df["postcode"].fillna("").astype(str)
    dup_domain = domains.notna() & domains.duplicated(keep=False)
    dup_name = (df["company_name"].notna() & df["postcode"].notna()) & names.duplicated(keep=False)
    return dup_domain | dup_name


def write_sample(df: pd.DataFrame, path: Path, seed: int, size: int) -> None:
    sample = df.sample(min(size, len(df)), random_state=seed)
    wb = Workbook()
    ws = wb.active
    ws.title = "Validation"
    headers = ["company_id", "website_url"]  # must not end in _check: that suffix marks answer columns
    for name in SAMPLE_FIELDS:
        headers += [name, f"{name}_source_url", f"{name}_check"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        if cell.value.endswith("_check"):
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
    for row in sample.to_dict("records"):
        out = [row["company_id"], row.get("website")]
        for name in SAMPLE_FIELDS:
            value = row.get(name)
            value = ", ".join(value) if isinstance(value, list) else (None if blank(value) else value)
            out += [value, row.get(f"{name}_source_url"), None]
        ws.append(out)
    dropdown = DataValidation(type="list", formula1='"' + ",".join(CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(dropdown)
    for col in ws.iter_cols(min_row=2):
        if ws.cell(1, col[0].column).value.endswith("_check"):
            for cell in col:
                dropdown.add(cell)
    ws.freeze_panes = "C2"
    wb.save(path)


def sample_has_answers(path: Path) -> bool:
    if not path.exists():
        return False
    ws = load_workbook(path).active
    checks = [c.column for c in ws[1] if str(c.value).endswith("_check")]
    return any(ws.cell(r, c).value for r in range(2, ws.max_row + 1) for c in checks)


def _row_answered(path: Path, row: int) -> bool:
    ws = load_workbook(path).active
    return any(ws.cell(row, c.column).value for c in ws[1] if str(c.value).endswith("_check"))


def accuracy(path: Path) -> dict:
    """Return per-field counts. Accuracy excludes 'not verifiable'."""
    ws = load_workbook(path).active
    columns = {c.value: c.column for c in ws[1]}
    result = {}
    for name in SAMPLE_FIELDS:
        answers = [str(ws.cell(r, columns[f"{name}_check"]).value or "").strip().lower() for r in range(2, ws.max_row + 1)]
        counts = {choice: answers.count(choice) for choice in CHOICES}
        judged = counts["correct"] + counts["wrong"]
        result[name] = {**counts, "accuracy": round(100 * counts["correct"] / judged, 1) if judged else None}
    return result


def report(ctx) -> None:
    sample = ctx.paths.outputs / SAMPLE_FILE
    if not sample_has_answers(sample):
        raise SystemExit(f"{sample} has no answers yet. Fill it in first.")
    result = accuracy(sample)
    totals = {c: sum(v[c] for v in result.values()) for c in CHOICES}
    judged = totals["correct"] + totals["wrong"]
    overall = round(100 * totals["correct"] / judged, 1) if judged else None
    write_json(ctx.paths.processed / "validation_results.json", {"fields": result, "totals": totals, "overall": overall})

    lines = ["# Validation report", "",
             f"Overall accuracy on judged fields: **{overall}%** (goal: at least 90%).",
             f"Judged: {judged}. Not verifiable, excluded from accuracy: {totals['not verifiable']}.", "",
             "| Field | Correct | Wrong | Not verifiable | Accuracy |", "|---|---:|---:|---:|---:|"]
    for name, v in result.items():
        value = "n/a" if v["accuracy"] is None else f"{v['accuracy']}%"
        lines.append(f"| {name} | {v['correct']} | {v['wrong']} | {v['not verifiable']} | {value} |")
    answered = sum(1 for r in range(2, load_workbook(sample).active.max_row + 1) if _row_answered(sample, r))
    lines[2:2] = [f"Rows checked by a person: {answered} of {load_workbook(sample).active.max_row - 1}.", ""]
    ai_check = read_json(ctx.paths.processed / "validation_ai_check.json")
    if ai_check:
        lines += ["", "## AI re-check (not independent)", "", ai_check["scope"] + ".", "",
                  f"Overall accuracy on judged fields: **{ai_check['overall']}%**.", "",
                  "| Field | Correct | Wrong | Not verifiable | Accuracy |", "|---|---:|---:|---:|---:|"]
        for name, v in ai_check["fields"].items():
            lines.append(f"| {name} | {v['correct']} | {v['wrong']} | {v['not verifiable']} | {v['accuracy']}% |")
    (ctx.paths.outputs / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Overall accuracy {overall}% on {judged} judged fields.")


def run(ctx) -> dict:
    df = load_leads(ctx)
    crawl = pd.read_csv(ctx.paths.processed / "crawl_status.csv", encoding="utf-8-sig")
    status = dict(zip(crawl["company_id"], crawl["crawl_status"]))
    duplicates = add_duplicate_flags(df)

    flags, reachable = [], []
    for i, row in enumerate(df.to_dict("records")):
        crawl_status = status.get(row["company_id"])
        row_flag = row_flags(row, ctx, crawl_status)
        if duplicates.iloc[i]:
            row_flag.append("possible_duplicate")
        flags.append(";".join(row_flag) or None)
        reachable.append(None if crawl_status in (None, "no_website") else crawl_status == "ok")
    df["qa_flags"] = flags
    df["website_reachable"] = reachable
    df.to_json(leads_path(ctx), orient="records", force_ascii=False, indent=1)

    settings = ctx.pipeline["validation"]
    sample = ctx.paths.outputs / SAMPLE_FILE
    if sample_has_answers(sample):
        sample_note = "kept existing filled-in sample"
    else:
        write_sample(df, sample, settings["seed"], settings["sample_size"])
        sample_note = "written"

    all_flags = pd.Series(";".join(f for f in flags if f).split(";")).value_counts()
    return {"leads": len(df), "sample": sample_note,
            "possible_duplicates": int(duplicates.sum()),
            **{f"flag_{k}": int(v) for k, v in all_flags.items() if k}}
