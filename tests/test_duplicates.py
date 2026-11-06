import sqlite3

import fork
from fork.llm import FakeLLM


def test_add_distinct_fork(loans_conn):
    r = fork.check("Which customers have an active loan?", "SELECT customer_id FROM loans WHERE status = 'A'", loans_conn, llm="none")
    dup = [f for f in r.forks if f.dependency.kind == "duplicates"]
    assert dup, "repeated customer ids should make DISTINCT a fork"
    rows = [len(b.rows) for _, b in dup[0].answers]
    assert rows[0] > rows[1]
    assert "distinct value" in dup[0].open_question


def test_primary_key_projection_is_not_a_duplicates_dependency(loans_conn):
    r = fork.check("List loan ids", "SELECT loan_id FROM loans", loans_conn, llm="none")
    assert not any(d.kind == "duplicates" for d in r.dependencies)


def test_count_distinct_toggle(loans_conn):
    r = fork.check("How many customers have loans?", "SELECT COUNT(customer_id) FROM loans", loans_conn, llm="none")
    dup = next(f for f in r.forks if f.dependency.kind == "duplicates")
    vals = [b.rows[0][0] for _, b in dup.answers]
    assert vals[0] == 100 and vals[1] == 20


def test_limit_one_ties(loans_conn):
    r = fork.check("Which loan has the smallest current balance?",
                   "SELECT loan_id FROM loans ORDER BY current_balance LIMIT 1", loans_conn, llm="none")
    dup = next(f for f in r.forks if f.dependency.readings[1].rewrite.type == "include_ties")
    rows = [len(b.rows) for _, b in dup.answers]
    assert rows[0] == 1 and rows[1] > 1  # every closed loan has balance 0
    assert "tied for first" in dup.open_question


def test_like_contains_and_exact_match():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE cards (id INTEGER PRIMARY KEY, name TEXT, keywords TEXT);
        INSERT INTO cards VALUES (1, 'a', 'Flying'), (2, 'b', 'Flying, Trample'), (3, 'c', 'Trample'), (4, 'd', 'Flying');
    """)
    r = fork.check("How many cards have flying?", "SELECT COUNT(*) FROM cards WHERE keywords = 'Flying'", conn, llm="none")
    f = next(f for f in r.forks if f.dependency.kind == "value_literal")
    alt = next(rd for rd, _ in f.answers if rd.rewrite.type == "like_contains")
    assert alt is not None
    assert [b.rows[0][0] for _, b in f.answers][:2] == [2, 3] or 3 in [b.rows[0][0] for _, b in f.answers]

    r = fork.check("How many cards have flying?", "SELECT COUNT(*) FROM cards WHERE keywords LIKE '%Flying%'", conn, llm="none")
    f = next(f for f in r.forks if f.dependency.kind == "value_literal")
    assert [b.rows[0][0] for _, b in f.answers] == [3, 2]


def test_llm_keeps_duplicates_alternative(loans_conn):
    answer = {"dependencies": [{"id": "d1", "agent_prior": 0.5, "alternatives": [
        {"description": "distinct customers", "prior": 0.4, "rewrite_type": "add_distinct", "value": None, "values": None, "column": None}]}],
        "new_column_choices": []}
    r = fork.check("Which customers have loans?", "SELECT customer_id FROM loans", loans_conn, llm=FakeLLM([answer]))
    assert len(r.forks) == 1 and r.forks[0].dependency.kind == "duplicates"
    assert r.forks[0].dependency.readings[1].prior == 0.4


def test_select_distinct_is_left_alone(loans_conn):
    r = fork.check("Which regions have loans?", "SELECT DISTINCT region FROM loans", loans_conn, llm="none")
    assert not any(d.kind == "duplicates" for d in r.dependencies)


def test_ties_that_repeat_the_same_row_do_not_fork(loans_conn):
    # every closed loan has current_balance 0, so the tie set is many rows, but all read 'C' or 'c'
    r = fork.check("What is the status of the loan with the smallest current balance?",
                   "SELECT status FROM loans WHERE status = 'C' ORDER BY current_balance LIMIT 1", loans_conn, llm="none")
    ties = [d for d in r.dependencies if d.readings[1].rewrite.type == "include_ties"]
    assert ties and ties[0] in r.collapsed
