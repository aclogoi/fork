"""Step 3 (half): apply a set of readings to a copy of the query AST."""

from __future__ import annotations

from sqlglot import exp

from .models import Dependency, Reading
from .parse import ParsedQuery


def _needs_quote(name: str) -> bool:
    return not name.replace("_", "a").isalnum() or name[0].isdigit()


def _ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def apply(pq: ParsedQuery, choice: dict[str, tuple[Dependency, Reading]], dialect: str) -> str:
    """choice maps dependency id -> (dependency, reading). Returns rewritten SQL."""
    tree = pq.root.copy()
    nodes = list(tree.walk())
    column_swaps: list[tuple[Dependency, Reading]] = []
    for dep, reading in choice.values():
        rw = reading.rewrite
        if rw.type == "identity":
            continue
        if rw.type == "replace_column":
            column_swaps.append((dep, reading))
            continue
        node = nodes[dep.node_id]
        _apply_node_rewrite(node, rw)
    for dep, reading in column_swaps:
        ref = nodes[dep.node_id]
        assert isinstance(ref, exp.Column)
        alias = (ref.table or "").lower()
        name = ref.name.lower()
        for col in list(tree.find_all(exp.Column)):
            if (col.table or "").lower() == alias and col.name.lower() == name:
                col.set("this", _ident(reading.rewrite.column))
    return tree.sql(dialect=dialect)


def _lit(v) -> exp.Expression:
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float)):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


def _apply_node_rewrite(node: exp.Expression, rw) -> None:
    t = rw.type
    if t == "replace_literal":
        if isinstance(node, (exp.EQ, exp.NEQ)):
            key = "expression" if isinstance(node.expression, exp.Literal) else "this"
            node.set(key, _lit(rw.value))
        elif isinstance(node, exp.In):
            node.set("expressions", [_lit(rw.value)])
        return
    if t == "literal_set":
        vals = [_lit(v) for v in rw.values or []]
        if isinstance(node, exp.EQ):
            col = node.this if isinstance(node.this, exp.Column) else node.expression
            node.replace(exp.In(this=col.copy(), expressions=vals))
        elif isinstance(node, exp.NEQ):
            col = node.this if isinstance(node.this, exp.Column) else node.expression
            node.replace(exp.Not(this=exp.In(this=col.copy(), expressions=vals)))
        elif isinstance(node, exp.In):
            node.set("expressions", vals)
        return
    if t == "include_nulls":
        col = _predicate_column(node)
        node.replace(exp.paren(exp.or_(node.copy(), exp.Is(this=col.copy(), expression=exp.Null()))))
        return
    if t == "empty_as_null":
        if isinstance(node, exp.Is):
            col = node.this
            node.replace(exp.paren(exp.or_(node.copy(), exp.EQ(this=col.copy(), expression=exp.Literal.string("")))))
        elif isinstance(node, exp.Not) and isinstance(node.this, exp.Is):
            col = node.this.this
            node.replace(exp.paren(exp.and_(node.copy(), exp.NEQ(this=col.copy(), expression=exp.Literal.string("")))))
        return
    if t == "coalesce_zero":
        if isinstance(node, exp.Avg):
            node.set("this", exp.Coalesce(this=node.this.copy(), expressions=[exp.Literal.number(0)]))
        return
    if t == "count_star":
        if isinstance(node, exp.Count):
            node.set("this", exp.Star())
        return
    raise ValueError(f"unknown rewrite {t}")


def _predicate_column(node: exp.Expression) -> exp.Column:
    if isinstance(node, exp.Not):
        node = node.this
    if isinstance(node, exp.In):
        return node.this
    if isinstance(node.this, exp.Column):
        return node.this
    return node.expression
