import fork
from fork.llm import FakeLLM, NullLLM


def test_heuristics_only_finds_case_variant_fork(loans_conn):
    r = fork.check("How many closed loans are there?", "SELECT COUNT(*) FROM loans WHERE status = 'C'", loans_conn, llm="none")
    assert r.base.rows == [(12,)]
    kinds = {f.dependency.kind for f in r.forks}
    assert "value_literal" in kinds
    vf = next(f for f in r.forks if f.dependency.kind == "value_literal")
    answers = [b.rows[0][0] for _, b in vf.answers]
    assert answers[0] == 12 and 22 in answers  # 'C' + 'c'
    assert r.llm_calls == 0


def test_literal_not_in_column(loans_conn):
    r = fork.check("How many closed loans?", "SELECT COUNT(*) FROM loans WHERE status = 'Closed'", loans_conn, llm="none")
    assert r.base.rows == [(0,)]
    vf = next(f for f in r.forks if f.dependency.kind == "value_literal")
    descs = [rd.description for rd, _ in vf.answers]
    assert any("'C'" in d for d in descs)


def test_llm_code_set_fork(loans_conn):
    answer = {
        "dependencies": [
            {"id": "d1", "agent_prior": 0.5, "alternatives": [
                {"description": "nonperforming means charged-off (C) or defaulted (D)", "prior": 0.5,
                 "rewrite_type": "literal_set", "values": ["C", "D"], "value": None, "column": None}
            ]}
        ],
        "new_column_choices": [],
    }
    llm = FakeLLM([answer])
    r = fork.check("How many nonperforming loans are there?", "SELECT COUNT(*) FROM loans WHERE status = 'C'", loans_conn, llm=llm)
    assert r.llm_calls == 1
    assert len(r.forks) == 1
    f = r.forks[0]
    assert [b.rows[0][0] for _, b in f.answers] == [12, 31]
    assert f.evidence is not None and f.evidence.rows is not None
    assert "Which value" in f.open_question
    text = fork.to_text(r)
    assert "31" in text and "12" in text


def test_column_choice_and_null_handling(loans_conn):
    r = fork.check("What is the average balance of active loans?",
                   "SELECT AVG(balance) FROM loans WHERE status != 'D'", loans_conn, llm="none")
    kinds = {d.kind for d in r.dependencies}
    assert "column_choice" in kinds
    assert "null_handling" in kinds  # status != 'D' with NULL statuses present
    nf = [f for f in r.forks if f.dependency.kind == "null_handling"]
    assert nf, "excluding NULL-status rows should change the average"
    cf = [f for f in r.forks if f.dependency.kind == "column_choice"]
    assert cf and any(rd.rewrite.column == "current_balance" for rd, _ in cf[0].answers)


def test_no_dependencies(loans_conn):
    r = fork.check("How many loans?", "SELECT COUNT(*) FROM loans", loans_conn, llm="none")
    assert not r.forks
    assert "No forks" in fork.to_text(r)


def test_json_roundtrip(loans_conn):
    r = fork.check("x", "SELECT COUNT(*) FROM loans WHERE status = 'C'", loans_conn, llm="none")
    import json
    d = json.loads(fork.to_json(r))
    assert d["flipped"] is True
    assert d["base"]["answer"] == "12"


def test_path_connection(loans_path):
    r = fork.check("x", "SELECT COUNT(*) FROM loans WHERE status = 'C'", loans_path, llm=NullLLM())
    assert r.base.rows == [(12,)]
