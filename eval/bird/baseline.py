"""Baseline text-to-SQL agent for BIRD, with the evidence field stripped.

Deliberately simple and documented: the model sees the DDL of every table in
the database, three sample rows per table, and the question. One call, SQL out.
Results are cached per question id so re-runs are free.

    python eval/bird/baseline.py --questions eval/bird/out/sample300_seed0.json
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import time

from common import OUT, append_jsonl, db_path, load_env, read_jsonl

from fork.db import Database
from fork.llm import default_llm

SYSTEM = """You are a careful data analyst writing SQLite SQL. You are given the schema of a database, a few sample rows per table, and a question. Write one SQL query that answers the question. The columns are not documented; use their names, types and sample values to decide what they mean. Return only the SQL."""

SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}

_schema_cache: dict[str, str] = {}


def schema_text(db_id: str) -> str:
    if db_id in _schema_cache:
        return _schema_cache[db_id]
    db = Database.connect(db_path(db_id))
    parts = [db.ddl()]
    for t in db.schema().values():
        cols, rows = db.sample_rows(t.name, 3)
        parts.append(f"-- sample rows from {t.name} ({', '.join(cols)}):")
        for r in rows:
            parts.append("--   " + " | ".join(_cell(x) for x in r))
    _schema_cache[db_id] = "\n".join(parts)
    return _schema_cache[db_id]


def _cell(x) -> str:
    s = repr(x)
    return s if len(s) <= 40 else s[:37] + "..."


def generate(llm, q: dict) -> dict:
    user = f"Schema:\n{schema_text(q['db_id'])}\n\nQuestion: {q['question']}"
    t0 = time.time()
    try:
        out = llm.json(SYSTEM, user, SCHEMA, max_tokens=2048)
        sql = out["sql"].strip().rstrip(";")
        err = None
    except Exception as e:
        sql, err = "", f"{type(e).__name__}: {e}"
    return {"question_id": q["question_id"], "db_id": q["db_id"], "sql": sql, "error": err,
            "model": getattr(llm, "model", llm.name), "elapsed": round(time.time() - t0, 2)}


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", default=str(OUT / "baseline.jsonl"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    import json
    from pathlib import Path

    qs = json.load(open(args.questions))
    if args.limit:
        qs = qs[: args.limit]
    out = Path(args.out)
    done = {r["question_id"] for r in read_jsonl(out) if not r.get("error")}
    todo = [q for q in qs if q["question_id"] not in done]
    print(f"{len(qs)} questions, {len(done)} cached, {len(todo)} to generate")
    llm = default_llm()
    if llm.name == "none":
        raise SystemExit("No LLM configured (set ANTHROPIC_API_KEY in .env)")
    for db_id in sorted({q["db_id"] for q in todo}):
        schema_text(db_id)
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for i, row in enumerate(ex.map(lambda q: generate(llm, q), todo), 1):
            append_jsonl(out, row)
            if i % 20 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")
    rows = read_jsonl(out)
    errs = sum(1 for r in rows if r.get("error"))
    print(f"done: {len(rows)} rows, {errs} errors -> {out}")


if __name__ == "__main__":
    main()
