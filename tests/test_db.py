import sqlite3

from fork.db import Database


def test_reserved_word_table_and_odd_column_names():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE "order" (id INTEGER PRIMARY KEY, "k symbol" TEXT, "Free Meal (K-12)" REAL);
        INSERT INTO "order" VALUES (1, 'SIPO', 1.5), (2, '', NULL), (3, 'SIPO', 2.0);
        """
    )
    db = Database.connect(conn)
    cols, rows = db.sample_rows("order", 2)
    assert cols[1] == "k symbol" and len(rows) == 2
    p = db.profile("order", "k symbol")
    assert p.row_count == 3 and p.empty_strings == 1 and p.top_values[0] == ("SIPO", 2)
    p2 = db.profile("order", "Free Meal (K-12)")
    assert p2.numeric and p2.null_rate > 0


def test_check_on_reserved_table():
    import fork

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE "order" (id INTEGER PRIMARY KEY, k_symbol TEXT, amount REAL);
        INSERT INTO "order" VALUES (1, 'SIPO', 10), (2, 'sipo', 20), (3, 'UVER', 5);
        """
    )
    r = fork.check("total SIPO amount", 'SELECT SUM(amount) FROM "order" WHERE k_symbol = \'SIPO\'', conn, llm="none")
    assert r.base.rows == [(10,)]
    assert r.forks and r.forks[0].evidence.rows is not None
