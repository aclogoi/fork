"""Step 1: parse the agent's SQL and find the places its answer depends on a reading.

Works over a sqlglot AST that has been *qualified* (every column carries its
table alias) so that a column reference can be resolved to a real table and
profiled. Node identity is the index into ``root.walk()``; ``rewrite.py``
re-walks a copy in the same order to find the same node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from .db import Database

PREDICATES = (exp.EQ, exp.NEQ, exp.In)


@dataclass
class ParsedQuery:
    root: exp.Expression                # qualified tree
    nodes: list[exp.Expression]         # root.walk() order
    aliases: dict[str, str]             # alias (lowercase) -> real table name
    derived: dict[str, exp.Select]      # subquery / CTE alias (lowercase) -> its SELECT
    ordered: bool                       # top-level ORDER BY present

    def sql(self, dialect: str) -> str:
        return self.root.sql(dialect=dialect, identify=True)


def parse(sql: str, db: Database) -> ParsedQuery:
    dialect = db.dialect
    root = sqlglot.parse_one(sql, read=dialect)
    try:
        q = qualify(
            root.copy(),
            schema=db.sqlglot_schema(),
            dialect=dialect,
            validate_qualify_columns=False,
            identify=False,
            quote_identifiers=False,
            expand_stars=False,
        )
    except Exception:
        q = root.copy()
        _best_effort_qualify(q, db)
    aliases: dict[str, str] = {}
    for t in q.find_all(exp.Table):
        real = db.find_table(t.name)
        if real is None:
            continue
        aliases[(t.alias or t.name).lower()] = real.name
    derived: dict[str, exp.Select] = {}
    for sub in q.find_all(exp.Subquery):
        if sub.alias and isinstance(sub.this, exp.Select):
            derived[sub.alias.lower()] = sub.this
    for cte in q.find_all(exp.CTE):
        if cte.alias and isinstance(cte.this, exp.Select):
            derived[cte.alias.lower()] = cte.this
    ordered = isinstance(q, exp.Select) and q.args.get("order") is not None
    if isinstance(q, (exp.Union, exp.Intersect, exp.Except)):
        ordered = q.args.get("order") is not None
    return ParsedQuery(root=q, nodes=list(q.walk()), aliases=aliases, derived=derived, ordered=ordered)


def _best_effort_qualify(tree: exp.Expression, db: Database) -> None:
    """If sqlglot's qualifier fails, attach a table to unqualified columns when unique."""
    tables = [db.find_table(t.name) for t in tree.find_all(exp.Table)]
    tables = [t for t in tables if t]
    for col in tree.find_all(exp.Column):
        if col.table:
            continue
        owners = [t for t in tables if db.find_column(t.name, col.name)]
        if len(owners) == 1:
            col.set("table", exp.to_identifier(owners[0].name))


# ---- column resolution --------------------------------------------------


def resolve(col: exp.Column, pq: ParsedQuery, db: Database, through_derived: bool = True, _depth: int = 0) -> tuple[str, str] | None:
    """Map a Column node to (real table name, real column name).

    With through_derived, a column of a subquery or CTE is followed to the
    projection it comes from, when that projection is a plain column.
    """
    if col.table:
        tl = col.table.lower()
        table = pq.aliases.get(tl)
        if not table and tl in pq.derived:
            if not through_derived or _depth > 5:
                return None
            sel = pq.derived[tl]
            for proj in sel.expressions:
                if proj.alias_or_name.lower() == col.name.lower():
                    inner = proj.this if isinstance(proj, exp.Alias) else proj
                    if isinstance(inner, exp.Column):
                        return resolve(inner, pq, db, True, _depth + 1)
                    return None
            return None
        if not table:
            real = db.find_table(col.table)
            table = real.name if real else None
    else:
        owners = [t for t in set(pq.aliases.values()) if db.find_column(t, col.name)]
        table = owners[0] if len(owners) == 1 else None
    if not table:
        return None
    real_col = db.find_column(table, col.name)
    if not real_col:
        return None
    return table, real_col


# ---- dependency candidates ----------------------------------------------


@dataclass
class LiteralSite:
    node_id: int
    table: str
    column: str
    column_node: exp.Column
    op: str                 # "=", "!=", "in", "not in"
    values: list[str]       # string literal(s)
    negated: bool


@dataclass
class ColumnSite:
    table: str
    column: str
    node_ids: list[int]     # every reference
    join_only: bool         # referenced only inside JOIN ... ON


@dataclass
class NullSite:
    node_id: int
    table: str
    column: str
    shape: str              # "neq", "not_in", "is_null", "is_not_null", "avg", "count"


def literal_sites(pq: ParsedQuery, db: Database) -> list[LiteralSite]:
    out = []
    for i, node in enumerate(pq.nodes):
        negated = isinstance(node.parent, exp.Not)
        if isinstance(node, (exp.EQ, exp.NEQ)):
            col, lit = _column_and_literal(node)
            if col is None or lit is None or not lit.is_string:
                continue
            r = resolve(col, pq, db)
            if not r:
                continue
            op = "!=" if isinstance(node, exp.NEQ) else "="
            out.append(LiteralSite(i, r[0], r[1], col, op, [lit.this], negated or op == "!="))
        elif isinstance(node, exp.In):
            col = node.this
            if not isinstance(col, exp.Column):
                continue
            lits = node.expressions
            if not lits or not all(isinstance(x, exp.Literal) and x.is_string for x in lits):
                continue
            r = resolve(col, pq, db)
            if not r:
                continue
            out.append(LiteralSite(i, r[0], r[1], col, "not in" if negated else "in", [x.this for x in lits], negated))
    return out


def _column_and_literal(node: exp.Binary) -> tuple[exp.Column | None, exp.Literal | None]:
    a, b = node.this, node.expression
    if isinstance(a, exp.Column) and isinstance(b, exp.Literal):
        return a, b
    if isinstance(b, exp.Column) and isinstance(a, exp.Literal):
        return b, a
    return None, None


def column_sites(pq: ParsedQuery, db: Database) -> list[ColumnSite]:
    seen: dict[tuple[str, str], ColumnSite] = {}
    for i, node in enumerate(pq.nodes):
        if not isinstance(node, exp.Column):
            continue
        r = resolve(node, pq, db, through_derived=False)
        if not r:
            continue
        in_join = _inside_join_on(node)
        site = seen.get(r)
        if site is None:
            seen[r] = ColumnSite(r[0], r[1], [i], in_join)
        else:
            site.node_ids.append(i)
            site.join_only = site.join_only and in_join
    return list(seen.values())


def _inside_join_on(node: exp.Expression) -> bool:
    p = node.parent
    child = node
    while p is not None:
        if isinstance(p, exp.Join) and p.args.get("on") is not None and child is p.args.get("on"):
            return True
        child, p = p, p.parent
    return False


def null_sites(pq: ParsedQuery, db: Database) -> list[NullSite]:
    out = []
    for i, node in enumerate(pq.nodes):
        if isinstance(node, exp.NEQ):
            col, lit = _column_and_literal(node)
            if col is not None:
                r = resolve(col, pq, db)
                if r:
                    out.append(NullSite(i, r[0], r[1], "neq"))
        elif isinstance(node, exp.Not) and isinstance(node.this, exp.In) and isinstance(node.this.this, exp.Column):
            r = resolve(node.this.this, pq, db)
            if r:
                out.append(NullSite(i, r[0], r[1], "not_in"))
        elif isinstance(node, exp.Is) and isinstance(node.expression, exp.Null) and isinstance(node.this, exp.Column):
            r = resolve(node.this, pq, db)
            if r:
                if isinstance(node.parent, exp.Not):
                    out.append(NullSite(pq.nodes.index(node.parent), r[0], r[1], "is_not_null"))
                else:
                    out.append(NullSite(i, r[0], r[1], "is_null"))
        elif isinstance(node, exp.Avg) and isinstance(node.this, exp.Column):
            r = resolve(node.this, pq, db)
            if r:
                out.append(NullSite(i, r[0], r[1], "avg"))
        elif isinstance(node, exp.Count) and isinstance(node.this, exp.Column):
            r = resolve(node.this, pq, db)
            if r:
                out.append(NullSite(i, r[0], r[1], "count"))
    return out


# ---- helpers used by readings -------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")


def name_tokens(name: str) -> set[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    toks = {_stem(t.lower()) for t in _TOKEN_RE.findall(s)}
    return toks - {"id", "the", "of", "a"}


def _stem(t: str) -> str:
    """types -> type, names -> name; leaves short and -ss words alone."""
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t
