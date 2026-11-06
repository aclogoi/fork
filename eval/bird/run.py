"""Run Fork over the baseline agent's SQL and compute the gate metrics.

    python eval/bird/run.py --questions eval/bird/out/sample300_seed0.json

Metrics (all over questions where the baseline produced runnable SQL):
  flip rate   share of questions with at least one fork (a plausible alternative
              reading changes the answer)
  catch rate  among questions the baseline got wrong, share where some fork's
              alternative branch returns the gold answer
  noise rate  among forks raised on questions the baseline got right, share the
              plausibility judge calls implausible (plus a 50-fork CSV for a human)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import random
import time
from pathlib import Path

from common import OUT, append_jsonl, db_path, load_env, read_jsonl

import fork
from fork.compare import results_equal, summarize
from fork.db import Database
from fork.llm import default_llm
from fork.report import to_dict

JUDGE_SYSTEM = """You judge whether an alternative reading of a question is plausible. You see a natural-language question, the SQL an agent wrote, and a fork: a column or value whose reading changes the answer, with the agent's reading and an alternative. Decide whether a careful analyst, reading the question and looking at the data, could reasonably have taken the question to mean the alternative. Answer plausible=true only if the alternative is a defensible interpretation of the question's wording, not merely a different query. Treat the columns and codes as undocumented: judge from the question, the SQL and the evidence rows shown, not from outside knowledge of what this dataset's codes mean."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"plausible": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["plausible", "reason"],
    "additionalProperties": False,
}


def gold_result(db: Database, sql: str):
    try:
        cols, rows, _ = db.execute(sql, timeout=60)
        return rows, None
    except Exception as e:
        return None, str(e)


def judge_fork(llm, q: dict, f: dict, baseline_sql: str) -> dict:
    alts = [a for a in f["answers"] if a["source"] != "agent"]
    agent = f["answers"][0]
    lines = [f"Question: {q['question']}", f"Agent SQL: {baseline_sql}", f"Fork: {f['dependency']['detail']} [{f['dependency']['kind']}]",
             f"Agent reading: {agent['description']} -> answer {agent['answer']}"]
    for a in alts:
        lines.append(f"Alternative reading: {a['description']} -> answer {a['answer']}")
    if f.get("evidence") and f["evidence"].get("rows"):
        lines.append("Evidence rows: " + "; ".join(", ".join(map(str, r)) for r in f["evidence"]["rows"][:6]))
    try:
        return llm.json(JUDGE_SYSTEM, "\n".join(lines), JUDGE_SCHEMA, max_tokens=512)
    except Exception as e:
        return {"plausible": None, "reason": f"judge failed: {e}"}


def process(q: dict, b: dict, llm, judge, timeout: float) -> dict:
    db = Database.connect(db_path(q["db_id"]))
    row = {"question_id": q["question_id"], "db_id": q["db_id"], "question": q["question"], "difficulty": q.get("difficulty"),
           "gold_sql": q["SQL"], "baseline_sql": b["sql"], "evidence": q.get("evidence")}
    gold_rows, gerr = gold_result(db, q["SQL"])
    row["gold_error"] = gerr
    row["gold_answer"] = summarize(gold_rows, None) if gold_rows is not None else None
    t0 = time.time()
    try:
        rep = fork.check(q["question"], b["sql"], db, llm=llm, timeout=timeout)
    except Exception as e:
        row.update({"check_error": f"{type(e).__name__}: {e}", "elapsed": round(time.time() - t0, 2)})
        return row
    row["elapsed"] = round(time.time() - t0, 2)
    row["baseline_error"] = rep.base.error
    row["baseline_answer"] = summarize(rep.base.rows, rep.base.columns)
    row["baseline_correct"] = (rep.base.ok and gold_rows is not None and results_equal(gold_rows, rep.base.rows))
    row["n_dependencies"] = len(rep.dependencies)
    row["n_forks"] = len(rep.forks)
    row["flipped"] = rep.flipped
    row["llm_calls"] = rep.llm_calls
    row["notes"] = rep.notes
    # catch: does any alternative branch hit the gold answer?
    caught = False
    for f in rep.forks:
        for r, br in f.answers:
            if r.source != "agent" and br.ok and gold_rows is not None and results_equal(gold_rows, br.rows):
                caught = True
    for br in rep.interaction_forks:
        if br.ok and gold_rows is not None and results_equal(gold_rows, br.rows):
            caught = True
    row["caught"] = caught
    d = to_dict(rep)
    row["forks"] = d["forks"]
    row["collapsed"] = [c["detail"] for c in d["collapsed"]]
    row["report_text"] = fork.to_text(rep)
    if judge is not None and rep.forks:
        row["judgements"] = [judge_fork(judge, q, f, b["sql"]) for f in d["forks"]]
    return row


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--baseline", default=str(OUT / "baseline.jsonl"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--llm", default=None, help="anthropic | openai | none")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    qs = json.load(open(args.questions))
    if args.limit:
        qs = qs[: args.limit]
    baseline = {r["question_id"]: r for r in read_jsonl(Path(args.baseline)) if r.get("sql")}
    tag = Path(args.questions).stem
    out = Path(args.out or OUT / f"run_{tag}.jsonl")
    done = {r["question_id"] for r in read_jsonl(out)}
    todo = [q for q in qs if q["question_id"] in baseline and q["question_id"] not in done]
    missing = [q["question_id"] for q in qs if q["question_id"] not in baseline]
    print(f"{len(qs)} questions, {len(done)} done, {len(todo)} to run, {len(missing)} without baseline SQL")
    llm = default_llm(args.llm)
    judge = None if (args.no_judge or llm.name == "none") else llm
    with cf.ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(process, q, baseline[q["question_id"]], llm, judge, args.timeout) for q in todo]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            append_jsonl(out, fut.result())
            if i % 10 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")
    summarize_run(out)


def summarize_run(out: Path) -> dict:
    rows = read_jsonl(out)
    usable = [r for r in rows if not r.get("check_error") and not r.get("baseline_error") and not r.get("gold_error")]
    n = len(usable)
    flipped = [r for r in usable if r["flipped"]]
    wrong = [r for r in usable if not r["baseline_correct"]]
    right = [r for r in usable if r["baseline_correct"]]
    caught = [r for r in wrong if r["caught"]]
    judged = [(j, r) for r in right for j in r.get("judgements", [])]
    implausible = [j for j, _ in judged if j.get("plausible") is False]
    forks_on_right = sum(r["n_forks"] for r in right)
    s = {
        "questions_run": len(rows), "usable": n,
        "baseline_accuracy": round(len(right) / n, 3) if n else None,
        "flip_rate": round(len(flipped) / n, 3) if n else None,
        "flip_rate_on_wrong": round(sum(1 for r in wrong if r["flipped"]) / len(wrong), 3) if wrong else None,
        "flip_rate_on_right": round(sum(1 for r in right if r["flipped"]) / len(right), 3) if right else None,
        "catch_rate": round(len(caught) / len(wrong), 3) if wrong else None,
        "noise_rate_llm_judge": round(len(implausible) / len(judged), 3) if judged else None,
        "forks_judged_on_right": len(judged), "forks_on_right": forks_on_right,
        "mean_dependencies": round(sum(r["n_dependencies"] for r in usable) / n, 2) if n else None,
        "mean_elapsed_s": round(sum(r["elapsed"] for r in usable) / n, 2) if n else None,
        "excluded": {"check_error": sum(1 for r in rows if r.get("check_error")),
                     "baseline_sql_error": sum(1 for r in rows if r.get("baseline_error")),
                     "gold_error": sum(1 for r in rows if r.get("gold_error"))},
    }
    kinds = {}
    for r in usable:
        for f in r["forks"]:
            k = f["dependency"]["kind"]
            kinds[k] = kinds.get(k, 0) + 1
    s["fork_kinds"] = kinds
    summary_path = out.with_suffix(".summary.json")
    json.dump(s, open(summary_path, "w"), indent=1)
    print(json.dumps(s, indent=1))
    write_label_sheet(rows, out.with_suffix(".labels.csv"))
    return s


def write_label_sheet(rows: list[dict], path: Path, n: int = 50, seed: int = 0) -> None:
    """50 random forks for a human to label plausible / implausible."""
    items = []
    for r in rows:
        for i, f in enumerate(r.get("forks", [])):
            alts = [a for a in f["answers"] if a["source"] != "agent"]
            items.append({
                "question_id": r["question_id"], "db_id": r["db_id"], "fork_index": i,
                "question": r["question"], "baseline_correct": r.get("baseline_correct"),
                "dependency": f["dependency"]["detail"], "kind": f["dependency"]["kind"],
                "agent_reading": f["answers"][0]["description"], "agent_answer": f["answers"][0]["answer"],
                "alternative_reading": " || ".join(a["description"] for a in alts),
                "alternative_answer": " || ".join(str(a["answer"]) for a in alts),
                "gold_answer": r.get("gold_answer"), "bird_evidence": r.get("evidence"),
                "llm_judge": " || ".join(str(j.get("plausible")) for j in r.get("judgements", [])[i:i + 1]),
                "human_plausible": "",
            })
    random.Random(seed).shuffle(items)
    items = items[:n]
    if not items:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(items[0].keys()))
        w.writeheader()
        w.writerows(items)
    print(f"label sheet: {path} ({len(items)} forks)")


if __name__ == "__main__":
    main()
