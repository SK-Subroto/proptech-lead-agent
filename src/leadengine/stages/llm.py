"""Stage 3b helpers. Claude Code reads the page files and writes the JSON itself.

Workflow, repeated until nothing remains:
    python -m leadengine llm next-batch      lists the next ~20 page files
    (Claude Code reads them and writes data/interim/llm/<id>.json)
    python -m leadengine llm validate        checks every file
"""

import json

import pandas as pd
from pydantic import ValidationError

from leadengine.schemas import CompanyExtraction, ExtractionBackend, missing_quotes
from leadengine.store import read_candidates, read_json

BACKENDS_NOT_BUILT = ("ollama", "anthropic_api")


class ClaudeCodeBackend(ExtractionBackend):
    """No API call: Claude Code does the reading under the user's Pro plan."""

    name = "claude_code"

    def prepare_batch(self, company_ids):
        return None


def get_backend(name: str) -> ExtractionBackend:
    if name == "claude_code":
        return ClaudeCodeBackend()
    if name in BACKENDS_NOT_BUILT:
        raise SystemExit(f"Backend '{name}' is a documented future option and is not implemented.")
    raise SystemExit(f"Unknown LLM backend '{name}'.")


def excluded_ids(ctx) -> set[str]:
    path = ctx.paths.processed / "excluded_after_crawl.csv"
    if not path.exists() or path.stat().st_size == 0:
        return set()
    return set(pd.read_csv(path, encoding="utf-8-sig")["company_id"])


def priority_queue(ctx) -> list[str]:
    """Companies needing extraction: most sources, then most pages, then most keyword hits."""
    skip = excluded_ids(ctx)
    entries = []
    for company in read_candidates(ctx).to_dict("records"):
        cid = company["company_id"]
        if cid in skip or not (ctx.paths.pages / f"{cid}.md").exists():
            continue
        rules = read_json(ctx.paths.rules / f"{cid}.json") or {}
        hits = sum(1 for key in ("owner_portal", "remote_reading_signal", "hiring_signal")
                   if (rules.get(key) or {}).get("value") == "mentioned")
        hits += len((rules.get("metering_providers") or {}).get("hits", []))
        entries.append((-(company.get("source_row_count") or 0), -len(rules.get("pages_used", [])), -hits, cid))
    return [e[3] for e in sorted(entries)]


def remaining(ctx) -> list[str]:
    return [cid for cid in priority_queue(ctx) if not (ctx.paths.llm / f"{cid}.json").exists()]


def _page_text(ctx, company_id: str) -> str:
    """Trimmed Markdown plus the full page text, so any crawled line can be evidence."""
    parts = []
    md = ctx.paths.pages / f"{company_id}.md"
    if md.exists():
        parts.append(md.read_text(encoding="utf-8"))
    doc = read_json(ctx.paths.pages / f"{company_id}.json")
    if doc:
        parts += [page["full_text"] for page in doc["pages"]]
    return "\n".join(parts)


def validate_all(ctx) -> dict[str, list[str]]:
    """Return {file: [problems]}; an empty dict means every record is valid."""
    problems = {}
    for path in sorted(ctx.paths.llm.glob("hv_*.json")):
        try:
            record = CompanyExtraction.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValidationError, json.JSONDecodeError) as exc:
            problems[path.name] = [str(exc)]
            continue
        issues = []
        if record.company_id != path.stem:
            issues.append("company_id does not match the file name")
        page_text = _page_text(ctx, path.stem)
        if page_text:
            issues += [f"{name}: evidence_quote not found in page text"
                       for name in missing_quotes(record, page_text)]
        if issues:
            problems[path.name] = issues
    return problems


def write_progress(ctx) -> tuple[int, int]:
    queue = priority_queue(ctx)
    done = [c for c in queue if (ctx.paths.llm / f"{c}.json").exists()]
    todo = [c for c in queue if c not in done]
    text = [f"# Stage 3b progress\n\nDone: {len(done)}\nRemaining: {len(todo)}\n", "## Remaining, in priority order\n"]
    text += [f"- {c}" for c in todo]
    (ctx.paths.llm / "_progress.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return len(done), len(todo)


def command(ctx, action: str) -> None:
    get_backend(ctx.pipeline["llm"]["backend"])
    if action == "brief":
        from leadengine.stages import llm_brief
        llm_brief.write_brief(ctx)
    elif action == "apply":
        from leadengine.stages import llm_brief
        llm_brief.apply_decisions(ctx)
    elif action == "next-batch":
        batch = remaining(ctx)[: ctx.pipeline["llm"]["batch_size"]]
        for cid in batch:
            print(ctx.paths.pages / f"{cid}.md")
        print(f"# {len(batch)} files in this batch")
    elif action == "validate":
        problems = validate_all(ctx)
        for name, issues in problems.items():
            print(name)
            for issue in issues:
                print("  -", issue)
        done, todo = write_progress(ctx)
        print(f"# {len(problems)} invalid files. Done {done}, remaining {todo}")
        if problems:
            raise SystemExit(1)
    elif action == "progress":
        done, todo = write_progress(ctx)
        print(f"Done {done}, remaining {todo}")
