"""Postgres adapter test. Skips unless a local server accepts connections.

Set FORK_TEST_PG to a postgresql:// URL for a database the test may create tables in.
Default: postgresql://localhost/fork_test (created if the server allows it).
"""

import os

import pytest

psycopg = pytest.importorskip("psycopg")

URL = os.environ.get("FORK_TEST_PG", "postgresql://localhost/fork_test")


def _connect():
    try:
        return psycopg.connect(URL, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError as e:
        if "does not exist" in str(e):
            try:
                admin = psycopg.connect(URL.rsplit("/", 1)[0] + "/postgres", autocommit=True, connect_timeout=2)
                admin.execute("CREATE DATABASE fork_test")
                admin.close()
                return psycopg.connect(URL, autocommit=True, connect_timeout=2)
            except Exception:
                pass
        pytest.skip(f"no postgres at {URL}: {e}")


@pytest.fixture
def pg_conn():
    conn = _connect()
    conn.execute("DROP TABLE IF EXISTS loans")
    conn.execute('DROP TABLE IF EXISTS "order"')
    conn.execute(
        """
        CREATE TABLE loans (loan_id INTEGER PRIMARY KEY, status TEXT, balance NUMERIC, current_balance NUMERIC, "Odd Name" TEXT)
        """
    )
    rows = [(i, "A", 100 + i, 90 + i, None) for i in range(50)]
    rows += [(50 + i, "C", 0, 0, "x") for i in range(12)]
    rows += [(62 + i, "D", 500, 400, "") for i in range(19)]
    rows += [(81 + i, "c", 0, 0, None) for i in range(10)]
    rows += [(91 + i, None, 10, 10, None) for i in range(4)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO loans VALUES (%s, %s, %s, %s, %s)", rows)
    conn.execute('CREATE TABLE "order" (id INTEGER PRIMARY KEY, k_symbol TEXT)')
    conn.execute("""INSERT INTO "order" VALUES (1, 'SIPO'), (2, 'sipo'), (3, '')""")
    yield conn
    conn.close()


def test_postgres_profile_and_check(pg_conn):
    import fork
    from fork.db import Database

    db = Database.connect(pg_conn)
    assert db.dialect == "postgres"
    p = db.profile("loans", "status")
    assert p.row_count == 95 and p.null_rate > 0 and ("C", 12) in p.top_values
    p2 = db.profile("loans", "Odd Name")
    assert p2.empty_strings == 19

    r = fork.check("How many closed loans?", "SELECT COUNT(*) FROM loans WHERE status = 'C'", pg_conn, llm="none")
    assert r.base.rows == [(12,)]
    vf = next(f for f in r.forks if f.dependency.kind == "value_literal")
    assert 22 in [b.rows[0][0] for _, b in vf.answers]
    assert vf.evidence.rows is not None

    r2 = fork.check("avg", "SELECT AVG(balance) FROM loans WHERE status != 'D'", pg_conn, llm="none")
    assert any(f.dependency.kind == "null_handling" for f in r2.forks)
    assert any(d.kind == "column_choice" for d in r2.dependencies)

    r3 = fork.check("x", """SELECT COUNT(*) FROM "order" WHERE k_symbol = 'SIPO'""", pg_conn, llm="none")
    assert r3.base.rows == [(1,)] and r3.forks


def test_postgres_url_connect(pg_conn):
    from fork.db import Database

    db = Database.connect(URL)
    assert db.dialect == "postgres" and "loans" in db.schema()


def test_postgres_timeout(pg_conn):
    from fork.db import Database, QueryTimeout

    db = Database.connect(pg_conn)
    with pytest.raises(QueryTimeout):
        db.execute("SELECT pg_sleep(2)", timeout=0.2)
