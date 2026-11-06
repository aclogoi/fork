"""Step 4: decide whether two query results are the same answer."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
from typing import Any

REL_TOL = 1e-6


def normalize_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if v == int(v) and abs(v) < 1e15:
            return int(v)
        return round(v, 9)
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, str):
        s = v.strip()
        # numeric strings compare as numbers (sqlite is loosely typed)
        try:
            f = float(s)
            if s and not s.lower().startswith(("inf", "nan")):
                return normalize_value(f)
        except ValueError:
            pass
        return s
    return v


def _row(r: tuple) -> tuple:
    return tuple(normalize_value(x) for x in r)


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9)
    return a == b


def _rows_close(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))


def results_equal(a: list[tuple] | None, b: list[tuple] | None, ordered: bool = False) -> bool:
    if a is None or b is None:
        return a is b
    if len(a) != len(b):
        return False
    na = [_row(r) for r in a]
    nb = [_row(r) for r in b]
    if ordered:
        return all(_rows_close(x, y) for x, y in zip(na, nb))
    if Counter(na) == Counter(nb):
        return True
    # tolerance-aware multiset match for float-bearing rows
    remaining = list(nb)
    for r in na:
        for j, s in enumerate(remaining):
            if _rows_close(r, s):
                remaining.pop(j)
                break
        else:
            return False
    return True


def summarize(rows: list[tuple] | None, columns: list[str] | None, max_rows: int = 5) -> str:
    """Short human rendering of a result."""
    if rows is None:
        return "<error>"
    if not rows:
        return "no rows"
    if len(rows) == 1 and len(rows[0]) == 1:
        return repr(normalize_value(rows[0][0]))
    head = ", ".join(columns or [])
    body = "; ".join(", ".join(repr(normalize_value(x)) for x in r) for r in rows[:max_rows])
    more = f" … (+{len(rows) - max_rows} rows)" if len(rows) > max_rows else ""
    return f"{len(rows)} rows [{head}]: {body}{more}"
