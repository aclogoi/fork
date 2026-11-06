import random
import sqlite3

import pytest


def build_loans(conn: sqlite3.Connection) -> None:
    """A small lending book with the classic ambiguities.

    status codes: A active, C (closed? charged-off?), D defaulted, '' and NULL present.
    balance vs current_balance vs original_balance look-alike columns.
    """
    conn.executescript(
        """
        CREATE TABLE loans (
            loan_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            status TEXT,
            balance REAL,
            current_balance REAL,
            original_balance REAL,
            region TEXT,
            closed_date TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name TEXT,
            segment TEXT
        );
        """
    )
    rng = random.Random(7)
    rows = []
    # 100 loans: 50 A, 12 C, 19 D, 10 'c', 5 '' , 4 NULL
    statuses = ["A"] * 50 + ["C"] * 12 + ["D"] * 19 + ["c"] * 10 + [""] * 5 + [None] * 4
    for i, st in enumerate(statuses, 1):
        orig = rng.randint(1000, 50000)
        cur = 0 if st in ("C", "c") else rng.randint(0, orig)
        bal = cur if rng.random() < 0.8 else orig
        rows.append((i, rng.randint(1, 20), st, bal, cur, orig, rng.choice(["east", "west", None]),
                     "2024-01-15" if st in ("C", "c") else None))
    conn.executemany("INSERT INTO loans VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO customers VALUES (?,?,?)", [(i, f"cust{i}", rng.choice(["retail", "sme"])) for i in range(1, 21)])
    conn.commit()


@pytest.fixture
def loans_conn():
    conn = sqlite3.connect(":memory:")
    build_loans(conn)
    return conn


@pytest.fixture
def loans_path(tmp_path):
    p = tmp_path / "loans.sqlite"
    conn = sqlite3.connect(p)
    build_loans(conn)
    conn.close()
    return str(p)
