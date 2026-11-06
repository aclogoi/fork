
from fork.db import Database
from fork.models import Dependency, Reading, Rewrite
from fork.parse import literal_sites, null_sites, parse
from fork.rewrite import apply


def _dep(pq, node_id, kind, table, col, rw):
    d = Dependency("d1", kind, table, col, node_id, "")
    d.readings = [Reading("d1.0", "", Rewrite("identity"), 0.5, "agent"), Reading("d1.1", "", rw, 0.5)]
    return d


def test_literal_set_rewrites_eq_to_in(loans_conn):
    db = Database.connect(loans_conn)
    pq = parse("SELECT COUNT(*) FROM loans WHERE status = 'C'", db)
    site = literal_sites(pq, db)[0]
    d = _dep(pq, site.node_id, "value_literal", "loans", "status", Rewrite("literal_set", values=["C", "D"]))
    sql = apply(pq, {"d1": (d, d.readings[1])}, "sqlite")
    assert "IN ('C', 'D')" in sql
    assert db.execute(sql)[1] == [(31,)]


def test_replace_column_everywhere(loans_conn):
    db = Database.connect(loans_conn)
    pq = parse("SELECT SUM(balance) FROM loans WHERE balance > 0", db)
    from fork.parse import column_sites
    site = next(s for s in column_sites(pq, db) if s.column == "balance")
    d = _dep(pq, site.node_ids[0], "column_choice", "loans", "balance", Rewrite("replace_column", column="current_balance"))
    sql = apply(pq, {"d1": (d, d.readings[1])}, "sqlite")
    assert sql.count("current_balance") == 2 and "balance >" not in sql.replace("current_balance", "")


def test_include_nulls(loans_conn):
    db = Database.connect(loans_conn)
    pq = parse("SELECT COUNT(*) FROM loans WHERE status != 'D'", db)
    site = next(s for s in null_sites(pq, db) if s.shape == "neq")
    d = _dep(pq, site.node_id, "null_handling", "loans", "status", Rewrite("include_nulls"))
    sql = apply(pq, {"d1": (d, d.readings[1])}, "sqlite")
    assert "IS NULL" in sql
    base = db.execute(pq.sql("sqlite"))[1][0][0]
    assert db.execute(sql)[1][0][0] == base + 4


def test_empty_as_null_and_count_star(loans_conn):
    db = Database.connect(loans_conn)
    pq = parse("SELECT COUNT(status) FROM loans WHERE status IS NOT NULL", db)
    sites = null_sites(pq, db)
    cnt = next(s for s in sites if s.shape == "count")
    isn = next(s for s in sites if s.shape == "is_not_null")
    d1 = _dep(pq, cnt.node_id, "null_handling", "loans", "status", Rewrite("count_star"))
    d2 = Dependency("d2", "null_handling", "loans", "status", isn.node_id, "")
    d2.readings = [Reading("d2.0", "", Rewrite("identity"), 0.5, "agent"), Reading("d2.1", "", Rewrite("empty_as_null"), 0.5)]
    sql = apply(pq, {"d1": (d1, d1.readings[1]), "d2": (d2, d2.readings[1])}, "sqlite")
    assert "COUNT(*)" in sql and "<> ''" in sql
    assert db.execute(sql)[1] == [(91,)]


def test_resolve_through_subquery_and_drop_predicate(loans_conn):
    db = Database.connect(loans_conn)
    sql = "SELECT COUNT(*) FROM (SELECT l.status AS st, l.balance FROM loans l WHERE l.balance > 0) WHERE st = 'C'"
    pq = parse(sql, db)
    sites = literal_sites(pq, db)
    assert [(s.table, s.column, s.values) for s in sites] == [("loans", "status", ["C"])]
    d = _dep(pq, sites[0].node_id, "value_literal", "loans", "status", Rewrite("drop_predicate"))
    out = apply(pq, {"d1": (d, d.readings[1])}, "sqlite")
    assert "TRUE" in out and db.execute(out)[1][0][0] > db.execute(pq.sql("sqlite"))[1][0][0]
