"""The entry point: fork.check(question, sql, conn)."""

from __future__ import annotations

from typing import Any

from .branch import BranchRunner, differs
from .compare import results_equal
from .db import Database
from .discriminate import gather_evidence
from .llm import LLM, NullLLM, default_llm
from .models import Fork, Report
from .parse import parse
from .readings import LLM_SCHEMA, LLM_SYSTEM, heuristic_dependencies, llm_prompt, merge_llm_readings


def check(
    question: str,
    sql: str,
    conn: Any,
    *,
    llm: LLM | None | str = None,
    evidence: bool = True,
    interactions: bool = True,
    timeout: float = 30.0,
    min_prior: float = 0.2,
) -> Report:
    """Check an agent's SQL for readings its answer depends on.

    question: the natural-language question the agent answered.
    sql: the SQL the agent generated.
    conn: a sqlite3 or psycopg connection, a path to a SQLite file, or a URL.
    llm: an LLM instance, a provider name ("anthropic", "openai", "none"), or
         None to pick from the environment. With "none" only heuristics run.
    min_prior: alternatives the model rates below this are dropped.
    """
    db = Database.connect(conn)
    model: LLM = default_llm(llm) if (llm is None or isinstance(llm, str)) else llm
    notes: list[str] = []
    llm_calls = 0

    pq = parse(sql, db)
    deps = heuristic_dependencies(pq, db)

    if deps and not isinstance(model, NullLLM):
        try:
            answer = model.json(LLM_SYSTEM, llm_prompt(question, sql, deps, pq, db), LLM_SCHEMA)
            llm_calls += 1
            deps = merge_llm_readings(deps, answer, pq, db, min_prior=min_prior)
        except Exception as e:
            notes.append(f"LLM reading pass failed, using heuristics only: {type(e).__name__}: {e}")
    elif deps:
        notes.append("No LLM configured: alternatives come from heuristics only.")
    deps = [d for d in deps if d.alternatives]

    runner = BranchRunner(pq, db, deps, timeout=timeout)
    base = runner.base()
    if base.ok:
        try:
            cols, rows, _ = db.execute(sql, timeout=timeout)
            if not results_equal(rows, base.rows, ordered=pq.ordered):
                notes.append("Re-rendered SQL differs from the original's result; branch comparisons use the re-rendered form.")
        except Exception as e:
            notes.append(f"Original SQL failed to run: {e}")

    per_dep, untested = runner.one_at_a_time()
    forks: list[Fork] = []
    collapsed = []
    for d in deps:
        results = per_dep[d.id]
        # every row tied for first is only a different answer if the tied rows differ
        distinct = any(r.rewrite.type == "include_ties" for r in d.alternatives)
        if any(differs(base, b, pq.ordered, distinct=distinct) for b in results[1:]):
            forks.append(Fork(dependency=d, branches=results))
        else:
            collapsed.append(d)

    interaction = []
    if interactions and len(forks) >= 2:
        extra = runner.interactions([f.dependency for f in forks])
        if extra is None:
            notes.append("Interaction pass skipped: too many reading combinations.")
        else:
            interaction = extra
    elif interactions and len(forks) < 2 and len(collapsed) >= 2:
        extra = runner.interactions(collapsed[:4])
        if extra:
            interaction = extra

    if evidence:
        for f in forks:
            gather_evidence(f, db, timeout=timeout)

    return Report(
        question=question, sql=sql, dialect=db.dialect, base=base, dependencies=deps,
        forks=forks, collapsed=collapsed, untested=untested, interaction_forks=interaction,
        llm_calls=llm_calls, notes=notes,
    )
