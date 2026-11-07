"""Score a hand-labelled fork sheet against the LLM judge.

    python eval/bird/labels.py eval/bird/out/run5_sample300_seed0.labels.csv

Fill the human_plausible column with y / n (anything else is skipped). The
noise rate counts only forks raised on questions the baseline got right, the
same population the LLM judge is scored on; forks on wrong answers are scored
separately as "pointed at the error" by comparing with gold_answer.
"""

from __future__ import annotations

import collections
import csv
import sys


def parse_label(v: str) -> bool | None:
    s = (v or "").strip().lower()
    if s in ("y", "yes", "true", "1", "p", "plausible"):
        return True
    if s in ("n", "no", "false", "0", "i", "implausible"):
        return False
    return None


def _bool(v: str) -> bool | None:
    """Spreadsheets rewrite True/False as TRUE/FALSE, 1/0 or yes/no on save."""
    return parse_label(v)


def main(path: str) -> None:
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        r["baseline_correct"] = {True: "True", False: "False"}.get(_bool(r["baseline_correct"]), r["baseline_correct"])
        r["llm_judge"] = {True: "True", False: "False"}.get(_bool(r["llm_judge"]), r["llm_judge"])
    labelled = [(r, parse_label(r["human_plausible"])) for r in rows]
    done = [(r, h) for r, h in labelled if h is not None]
    print(f"{len(rows)} forks, {len(done)} labelled")
    if not done:
        return
    right = [(r, h) for r, h in done if r["baseline_correct"] == "True"]
    wrong = [(r, h) for r, h in done if r["baseline_correct"] == "False"]
    if right:
        noise = sum(1 for _, h in right if not h)
        print(f"human noise rate (forks on correct answers): {noise}/{len(right)} = {noise / len(right):.0%}")
        judged = [(r, h) for r, h in right if r["llm_judge"] in ("True", "False")]
        if judged:
            agree = sum(1 for r, h in judged if (r["llm_judge"] == "True") == h)
            jn = sum(1 for r, _ in judged if r["llm_judge"] == "False")
            print(f"LLM judge on the same forks: noise {jn}/{len(judged)} = {jn / len(judged):.0%}; agrees with you on {agree}/{len(judged)}")
            for r, h in judged:
                if (r["llm_judge"] == "True") != h:
                    print(f"  disagree q{r['question_id']} ({r['kind']}): you={'plausible' if h else 'implausible'} judge={r['llm_judge']}  {r['question'][:70]}")
    if wrong:
        plausible = sum(1 for _, h in wrong if h)
        print(f"forks on wrong answers you call plausible: {plausible}/{len(wrong)}")
    by_kind = collections.defaultdict(lambda: [0, 0])
    for r, h in done:
        by_kind[r["kind"]][1] += 1
        by_kind[r["kind"]][0] += not h
    print("implausible by kind:", {k: f"{a}/{b}" for k, (a, b) in by_kind.items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval/bird/out/run5_sample300_seed0.labels.csv")
