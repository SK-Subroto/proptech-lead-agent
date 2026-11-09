"""Small file helpers shared by the stages."""

import json
from pathlib import Path

import pandas as pd


def read_candidates(ctx) -> pd.DataFrame:
    df = pd.read_csv(ctx.paths.processed / "candidates.csv", dtype={"postcode": "string"}, encoding="utf-8-sig")
    return df.head(ctx.limit) if ctx.limit else df


def read_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA or value == ""
