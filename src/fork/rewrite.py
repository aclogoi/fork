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
    # deepest nodes first, so a rewrite that copies or replaces a subtree (include_ties,
    # drop_predicate) sees the rewrites already applied inside it
    for dep, reading in sorted(choice.values(), key=lambda dr: -dr[0].node_id):
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
    return tree.sql(dialect=dialect, identify=True)


def _lit(v) -> exp.Expression:
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float)):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


def _like(col: exp.Expression, v) -> exp.Expression:
    return exp.Like(this=col.copy(), expression=exp.Literal.string(f"%{v}%"))


def _apply_node_rewrite(node: exp.Expression, rw) -> None:
    t = rw.type
    if t == "replace_literal":
        if isinstance(node, (exp.EQ, exp.NEQ)):
            key = "expression" if isinstance(node.expression, exp.Literal) else "this"
            node.set(key, _lit(rw.value))
        elif isinstance(node, exp.In):
            node.set("expressions", [_lit(rw.value)])
        elif isinstance(node, (exp.Like, exp.ILike)):
            node.set("expression", exp.Literal.string(f"%{rw.value}%"))
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
        elif isinstance(node, (exp.Like, exp.ILike)):
            likes = [_like(node.this, v) for v in rw.values or []]
            node.replace(exp.paren(exp.or_(*likes)) if len(likes) > 1 else likes[0])
        return
    if t == "like_contains":
        if isinstance(node, (exp.EQ, exp.NEQ)):
            col = node.this if isinstance(node.this, exp.Column) else node.expression
            lit = node.expression if isinstance(node.expression, exp.Literal) else node.this
            new = _like(col, lit.this)
            node.replace(exp.Not(this=new) if isinstance(node, exp.NEQ) else new)
        elif isinstance(node, exp.In):
            likes = [_like(node.this, x.this) for x in node.expressions if isinstance(x, exp.Literal)]
            if likes:
                node.replace(exp.paren(exp.or_(*likes)) if len(likes) > 1 else likes[0])
        return
    if t == "exact_match":
        if isinstance(node, (exp.Like, exp.ILike)) and isinstance(node.expression, exp.Literal):
            core = node.expression.this.strip("%")
            node.replace(exp.EQ(this=node.this.copy(), expression=exp.Literal.string(core)))
        return
    if t == "add_distinct":
        if isinstance(node, exp.Select):
            node.set("distinct", exp.Distinct())
        return
    if t == "drop_distinct":
        if isinstance(node, exp.Select):
            node.set("distinct", None)
        return
    if t == "count_distinct":
        if isinstance(node, exp.Count) and isinstance(node.this, exp.Column) and not isinstance(node.this.this, exp.Star):
            node.set("this", exp.Distinct(expressions=[node.this.copy()]))
        return
    if t == "count_all":
        if isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct) and len(node.this.expressions) == 1:
            node.set("this", node.this.expressions[0].copy())
        return
    if t == "include_ties":
        if isinstance(node, exp.Select):
            _include_ties(node)
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
    if t == "drop_predicate":
        target = node.parent if isinstance(node.parent, exp.Not) else node
        target.replace(exp.true())
        return
    raise ValueError(f"unknown rewrite {t}")


def _include_ties(sel: exp.Select) -> None:
    """ORDER BY e1, e2 LIMIT 1  ->  every row whose (e1, e2) equals the first row's.

    The first row's key comes from a copy of the query that projects only the
    ordering expressions. The key test goes in HAVING when the query groups or
    the key aggregates, otherwise in WHERE. LIMIT is removed; ORDER BY stays.
    """
    order = sel.args.get("order")
    if order is None or not order.expressions:
        return
    keys = [_resolve_order_expr(o.this, sel) for o in order.expressions]
    sub = sel.copy()
    sub.set("with", None)
    sub.set("expressions", [k.copy() for k in keys])
    sub.set("order", exp.Order(expressions=[
        exp.Ordered(this=k.copy(), desc=o.args.get("desc"), nulls_first=o.args.get("nulls_first"))
        for k, o in zip(keys, order.expressions, strict=False)
    ]))
    sub.set("limit", exp.Limit(expression=exp.Literal.number(1)))
    sub.set("distinct", None)
    lhs = keys[0].copy() if len(keys) == 1 else exp.Tuple(expressions=[k.copy() for k in keys])
    cond = exp.EQ(this=lhs, expression=exp.Subquery(this=sub))
    sel.set("limit", None)
    aggregated = sel.args.get("group") is not None or any(k.find(exp.AggFunc) is not None or isinstance(k, exp.AggFunc) for k in keys)
    if aggregated:
        having = sel.args.get("having")
        sel.set("having", exp.Having(this=exp.and_(having.this, cond) if having is not None else cond))
    else:
        where = sel.args.get("where")
        sel.set("where", exp.Where(this=exp.and_(where.this, cond) if where is not None else cond))


def _resolve_order_expr(e: exp.Expression, sel: exp.Select) -> exp.Expression:
    """ORDER BY cnt / ORDER BY 2 -> the projected expression they name."""
    projections = sel.expressions
    if isinstance(e, exp.Literal) and not e.is_string and e.this.isdigit():
        idx = int(e.this) - 1
        if 0 <= idx < len(projections):
            p = projections[idx]
            return (p.this if isinstance(p, exp.Alias) else p).copy()
    if isinstance(e, exp.Column) and not e.table:
        for p in projections:
            if isinstance(p, exp.Alias) and p.alias.lower() == e.name.lower():
                return p.this.copy()
    return e.copy()


def _predicate_column(node: exp.Expression) -> exp.Column:
    if isinstance(node, exp.Not):
        node = node.this
    if isinstance(node, exp.In):
        return node.this
    if isinstance(node.this, exp.Column):
        return node.this
    return node.expression
