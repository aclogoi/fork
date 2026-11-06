"""Shared paths and helpers for the BIRD evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

RAW = ROOT / "data" / "bird" / "raw" / "dev_20240627"
OUT = ROOT / "eval" / "bird" / "out"
OUT.mkdir(parents=True, exist_ok=True)


def load_env():
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def dev_questions() -> list[dict]:
    return json.load(open(RAW / "dev.json"))


def db_path(db_id: str) -> str:
    return str(RAW / "dev_databases" / db_id / f"{db_id}.sqlite")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in open(path) if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
