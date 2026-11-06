"""Step 3 and 4: run the query under each reading and compare answers.

v0 strategy: whole-query rewrite per reading combination. Two passes:

1. One-at-a-time: for every alternative reading, run the query with that one
   dependency switched and everything else at the agent's reading. A
   dependency forks if any of its alternatives changes the answer.
2. Interaction pass (only when the full product is at most ``leaf_cap``
   leaves): run every combination, to catch answers that only move when two
   readings change together. Reported separately as interaction forks.
"""

from __future__ import annotations

import itertools
import time

from .compare import results_equal
from .db import Database, QueryTimeout
from .models import BranchResult, Dependency, Reading
from .parse import ParsedQuery
from .rewrite import apply

LEAF_CAP = 16
MAX_ONE_AT_A_TIME = 24


class BranchRunner:
    def __init__(self, pq: ParsedQuery, db: Database, deps: list[Dependency], timeout: float = 30.0, max_rows: int = 10_000):
        self.pq = pq
        self.db = db
        self.deps = deps
        self.timeout = timeout
        self.max_rows = max_rows
        self._cache: dict[tuple[str, ...], BranchResult] = {}

    def agent_choice(self) -> dict[str, tuple[Dependency, Reading]]:
        return {d.id: (d, d.readings[0]) for d in self.deps}

    def run(self, choice: dict[str, tuple[Dependency, Reading]]) -> BranchResult:
        key = tuple(r.id for _, r in (choice[d.id] for d in self.deps))
        if key in self._cache:
            return self._cache[key]
        try:
            sql = apply(self.pq, choice, self.db.dialect)
        except Exception as e:  # rewrite failure is a branch error, not a crash
            res = BranchResult(key, "", None, None, error=f"rewrite failed: {e}")
            self._cache[key] = res
            return res
        t0 = time.perf_counter()
        try:
            cols, rows, truncated = self.db.execute(sql, timeout=self.timeout, max_rows=self.max_rows)
            res = BranchResult(key, sql, rows, cols, elapsed_ms=(time.perf_counter() - t0) * 1000, truncated=truncated)
        except QueryTimeout as e:
            res = BranchResult(key, sql, None, None, error=str(e), elapsed_ms=(time.perf_counter() - t0) * 1000)
        except Exception as e:
            res = BranchResult(key, sql, None, None, error=f"{type(e).__name__}: {e}", elapsed_ms=(time.perf_counter() - t0) * 1000)
        self._cache[key] = res
        return res

    def base(self) -> BranchResult:
        return self.run(self.agent_choice())

    def one_at_a_time(self) -> tuple[dict[str, list[BranchResult]], list[str]]:
        """Returns {dep_id: [result per reading]} and the list of untested reading ids."""
        base = self.base()
        out: dict[str, list[BranchResult]] = {}
        untested: list[str] = []
        budget = MAX_ONE_AT_A_TIME
        # most promising alternatives first
        order = sorted(self.deps, key=lambda d: -max((r.prior for r in d.alternatives), default=0))
        for d in order:
            results = [base]
            for r in d.alternatives:
                if budget <= 0:
                    untested.append(r.id)
                    results.append(BranchResult((), "", None, None, error="untested: branch cap"))
                    continue
                choice = self.agent_choice()
                choice[d.id] = (d, r)
                results.append(self.run(choice))
                budget -= 1
            out[d.id] = results
        return out, untested

    def interactions(self, forked: list[Dependency]) -> list[BranchResult] | None:
        """Full product over the forked dependencies' readings if small enough. None if skipped."""
        if len(forked) < 2:
            return []
        leaves = 1
        for d in forked:
            leaves *= len(d.readings)
        if leaves > LEAF_CAP:
            return None
        base = self.base()
        extra: list[BranchResult] = []
        for combo in itertools.product(*[d.readings for d in forked]):
            if sum(1 for r in combo if r.source != "agent") < 2:
                continue  # already covered by the one-at-a-time pass
            choice = self.agent_choice()
            for d, r in zip(forked, combo, strict=False):
                choice[d.id] = (d, r)
            res = self.run(choice)
            if res.ok and base.ok and not results_equal(base.rows, res.rows, ordered=self.pq.ordered):
                extra.append(res)
        return extra


def differs(base: BranchResult, other: BranchResult, ordered: bool) -> bool:
    """A branch 'flips' if it returns a different answer, or errors where the base did not."""
    if other.error and other.error.startswith("untested"):
        return False
    if not base.ok:
        return other.ok
    if not other.ok:
        return True
    return not results_equal(base.rows, other.rows, ordered=ordered)
