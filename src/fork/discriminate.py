"""Step 5: for each fork, a query whose result tells the readings apart.

v0 is the dumbest working form: show the data that an analyst would look at to
decide. For a value fork, the profile of the competing values with the other
columns that co-vary; for a column fork, how often the two columns agree; for a
null fork, how many rows the choice affects.
"""

from __future__ import annotations

from sqlglot import exp

from .db import Database
from .models import Dependency, Evidence, Fork


def discriminating_query(dep: Dependency, db: Database) -> tuple[str, str]:
    """Returns (sql, open_question)."""
    t = db.find_table(dep.table)
    tbl = exp.to_table(t.name)
    col = exp.column(dep.column)
    d = db.dialect
    if dep.kind == "value_literal":
        values = _values_in_play(dep)
        selects = [col, exp.Count(this=exp.Star()).as_("n")]
        for other, oty in t.columns:
            if other == dep.column or other in t.primary_key or len(selects) >= 8:
                continue
            cls = _type_class(oty)
            oc = exp.column(other)
            if cls == "number":
                selects.append(exp.Avg(this=oc).as_(f"avg_{_short(other)}"))
            elif cls == "date":
                selects.append(exp.Min(this=oc).as_(f"min_{_short(other)}"))
                selects.append(exp.Max(this=oc).as_(f"max_{_short(other)}"))
        q = exp.select(*selects).from_(tbl)
        if values:
            q = q.where(exp.In(this=col, expressions=[_lit(v) for v in values]))
        q = q.group_by(col).order_by(exp.Ordered(this=exp.column("n"), desc=True)).limit(20)
        return q.sql(dialect=d), f"Which value(s) of {dep.table}.{dep.column} does the question mean? Compare the rows below."
    if dep.kind == "column_choice":
        others = [r.rewrite.column for r in dep.alternatives if r.rewrite.column]
        selects = [exp.Count(this=exp.Star()).as_("n")]
        for o in others:
            oc = exp.column(o)
            selects.append(exp.Sum(this=exp.Case().when(exp.EQ(this=col, expression=oc), exp.Literal.number(1)).else_(exp.Literal.number(0))).as_(f"same_as_{_short(o)}"))
            selects.append(exp.Count(this=exp.Distinct(expressions=[oc])).as_(f"distinct_{_short(o)}"))
        selects.append(exp.Count(this=exp.Distinct(expressions=[col])).as_(f"distinct_{_short(dep.column)}"))
        q = exp.select(*selects).from_(tbl)
        return q.sql(dialect=d), f"Does the question refer to {dep.column} or to {', '.join(others)}? Check how often they agree and what each holds."
    # null_handling
    selects = [
        exp.Count(this=exp.Star()).as_("rows"),
        exp.Sum(this=exp.Case().when(exp.Is(this=col, expression=exp.Null()), exp.Literal.number(1)).else_(exp.Literal.number(0))).as_("nulls"),
        exp.Sum(this=exp.Case().when(exp.EQ(this=col, expression=exp.Literal.string("")), exp.Literal.number(1)).else_(exp.Literal.number(0))).as_("empties"),
    ]
    q = exp.select(*selects).from_(tbl)
    return q.sql(dialect=d), f"Should rows with missing {dep.table}.{dep.column} be counted? {dep.profile.null_rate:.0%} are NULL." if dep.profile else "Should rows with missing values be counted?"


def gather_evidence(fork: Fork, db: Database, timeout: float = 30.0) -> None:
    try:
        sql, question = discriminating_query(fork.dependency, db)
    except Exception as e:
        fork.evidence = Evidence("", None, None, error=f"could not build discriminating query: {e}")
        return
    fork.open_question = question
    try:
        cols, rows, _ = db.execute(sql, timeout=timeout, max_rows=50)
        fork.evidence = Evidence(sql, cols, rows)
    except Exception as e:
        fork.evidence = Evidence(sql, None, None, error=str(e))


def _values_in_play(dep: Dependency) -> list:
    vals = []
    for r in dep.readings:
        rw = r.rewrite
        if rw.type == "replace_literal":
            vals.append(rw.value)
        elif rw.type == "literal_set":
            vals.extend(rw.values or [])
    # agent's own literal(s) from the detail string are not stored; profile top values fill in
    if dep.profile:
        for v in dep.profile.values():
            if v in vals:
                continue
        # include agent literal: parse from detail "table.col = 'C'"
    import re

    for m in re.finditer(r"'((?:[^']|'')*)'", dep.detail):
        vals.append(m.group(1).replace("''", "'"))
    seen, out = set(), []
    for v in vals:
        k = str(v)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out[:12]


def _lit(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


def _short(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)[:20]


def _type_class(declared: str) -> str:
    d = (declared or "").upper()
    if any(k in d for k in ("DATE", "TIME")):
        return "date"
    if any(k in d for k in ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE")):
        return "number"
    return "text"
