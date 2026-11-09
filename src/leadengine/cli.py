"""Command line entry point: python -m leadengine <command>."""

import argparse
import importlib
import logging
import time

from leadengine.config import load_context

# Stage 3b (llm) is done by Claude Code between `rules` and `geo`.
# See README.md for the order.
STAGES = ["collect", "crawl", "rules", "geo", "contacts", "score", "validate", "export", "map"]


def run_stage(name: str, ctx) -> dict:
    module = importlib.import_module(f"leadengine.stages.{name}")
    started = time.perf_counter()
    counts = module.run(ctx)
    ctx.record_stage(name, counts, time.perf_counter() - started)
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="leadengine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--city", default="Berlin")
        p.add_argument("--limit", type=int, help="process only the first N companies (dry run)")

    p_run = sub.add_parser("run", help="run several stages in order")
    add_common(p_run)
    p_run.add_argument("--stages", default=",".join(STAGES), help="comma separated stage names")

    for stage in STAGES:
        add_common(sub.add_parser(stage, help=f"run the {stage} stage"))

    p_llm = sub.add_parser("llm", help="helpers for Stage 3b, done by Claude Code")
    add_common(p_llm)
    p_llm.add_argument("action", choices=["next-batch", "validate", "progress", "brief", "apply"])

    add_common(sub.add_parser("purge-google", help="delete stored Google responses"))
    add_common(sub.add_parser("validation-report", help="score the filled-in validation sample"))
    add_common(sub.add_parser("report", help="write outputs/run_summary.md"))

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    ctx = load_context(args.city, args.limit)

    if args.command == "run":
        for stage in [s.strip() for s in args.stages.split(",") if s.strip()]:
            if stage not in STAGES:
                raise SystemExit(f"Unknown stage '{stage}'. Choose from: {', '.join(STAGES)}")
            run_stage(stage, ctx)
    elif args.command in STAGES:
        run_stage(args.command, ctx)
    elif args.command == "llm":
        from leadengine.stages import llm
        llm.command(ctx, args.action)
    elif args.command == "purge-google":
        from leadengine.sources import google_places
        google_places.purge(ctx)
    elif args.command == "validation-report":
        from leadengine.stages import validate
        validate.report(ctx)
    elif args.command == "report":
        from leadengine.stages import export
        export.write_run_summary(ctx)
