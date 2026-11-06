"""Stratified sample of BIRD dev: ~N questions, proportional per database, fixed seed.

    python eval/bird/sample.py --n 300 --seed 0
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict

from common import OUT, dev_questions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="take every question")
    args = ap.parse_args()
    qs = dev_questions()
    if args.all:
        chosen = qs
    else:
        by_db = defaultdict(list)
        for q in qs:
            by_db[q["db_id"]].append(q)
        rng = random.Random(args.seed)
        chosen = []
        for db_id, items in sorted(by_db.items()):
            k = max(10, round(args.n * len(items) / len(qs)))
            rng.shuffle(items)
            chosen.extend(items[:k])
        chosen.sort(key=lambda q: q["question_id"])
    name = "all" if args.all else f"sample{args.n}_seed{args.seed}"
    path = OUT / f"{name}.json"
    json.dump(chosen, open(path, "w"), indent=1)
    print(f"{len(chosen)} questions -> {path}")


if __name__ == "__main__":
    main()
