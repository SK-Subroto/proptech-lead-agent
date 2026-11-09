"""Loads YAML config and defines the project's file locations."""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from leadengine.ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("leadengine")


class Paths:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.config = root / "config"
        self.data = root / "data"
        self.raw = self.data / "raw"
        self.raw_osm = self.raw / "osm"
        self.raw_google = self.raw / "google"
        self.raw_html = self.raw / "html"
        self.raw_geo = self.raw / "geo"
        self.interim = self.data / "interim"
        self.pages = self.interim / "pages"
        self.rules = self.interim / "rules"
        self.llm = self.interim / "llm"
        self.processed = self.data / "processed"
        self.outputs = root / "outputs"

    def ensure(self) -> None:
        for path in (
            self.raw_osm, self.raw_google, self.raw_html, self.raw_geo,
            self.pages, self.rules, self.llm, self.processed, self.outputs,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Context:
    """Everything a stage needs."""

    city: dict
    pipeline: dict
    scoring: dict
    keywords: dict
    paths: Paths
    ledger: Ledger
    google_api_key: str | None = None
    limit: int | None = None

    def record_stage(self, stage: str, counts: dict, seconds: float) -> None:
        """Log a stage's counts and keep them for run_summary.md."""
        path = self.paths.processed / "stage_counts.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[stage] = {
            "counts": counts,
            "seconds": round(seconds, 1),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        log.info("%s finished in %.1fs: %s", stage, seconds, counts)


def load_context(city: str = "Berlin", limit: int | None = None, root: Path = ROOT) -> Context:
    load_dotenv(root / ".env")
    paths = Paths(root)
    paths.ensure()
    city_path = paths.config / "cities" / f"{city.lower()}.yaml"
    if not city_path.exists():
        raise SystemExit(f"No config for city '{city}'. Expected {city_path}.")
    return Context(
        city=_read_yaml(city_path),
        pipeline=_read_yaml(paths.config / "pipeline.yaml"),
        scoring=_read_yaml(paths.config / "scoring.yaml"),
        keywords=_read_yaml(paths.config / "keywords.yaml"),
        paths=paths,
        ledger=Ledger(paths.processed / "request_ledger.json"),
        google_api_key=os.environ.get("GOOGLE_MAPS_API_KEY") or None,
        limit=limit,
    )
