# Fork

An ambiguity checker for text-to-SQL agents.

Fork wraps any text-to-SQL agent and shows where its answer depends on an unstated reading of a column or value. For a question, Fork finds the ambiguous terms the generated query touches, runs the query under each plausible reading, and reports the fork:

```
Question: How many nonperforming loans are there?
Agent answer: 12

FORK 1: loans.status = 'C'  [value literal]
  12    if status = 'C' as written                      (prior 0.30, agent)
  19    if nonperforming loans are coded 'D', not 'C'   (prior 0.35, llm)
  22    if 'C' and 'c' are the same code                (prior 0.30, llm)
  Open: Which value(s) of loans.status does the question mean?
  Evidence: SELECT status, COUNT(*) AS n, AVG(balance), ... FROM loans WHERE status IN ('D','C','c') GROUP BY status
    D | 19 | 14710 | ...
    C | 12 |  8252 | ...
    c | 10 |  9031 | ...
```

The agent's answer is never suppressed, only annotated. Readings that do not change the answer are collapsed and never reported.

## Why

Text-to-SQL agents fail on column semantics far more than on SQL syntax, and they fail silently. Given an undocumented schema the agent picks one reading of `status = 'C'` or of a `balance` column, writes a correct query against that reading, and returns a confident number. Nothing in the output says a choice was made. Fork makes the choice visible and shows what would settle it.

## Install

```
pip install fork-sql            # SQLite
pip install "fork-sql[postgres]" # + Postgres
pip install "fork-sql[mcp]"      # + MCP server
```

Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` with `FORK_LLM_PROVIDER=openai`) for the reading proposer. Without a key Fork still runs, using heuristics only.

## Use

Python:

```python
import fork

report = fork.check(
    "How many nonperforming loans are there?",
    "SELECT COUNT(*) FROM loans WHERE status = 'C'",
    "loans.sqlite",                      # path, sqlite:// or postgresql:// URL, or a DB-API connection
)
print(fork.to_text(report))
report.flipped                           # True if any plausible reading changes the answer
report.forks[0].answers                  # [(reading, branch result), ...]
```

CLI:

```
fork check --db loans.sqlite -q "How many nonperforming loans are there?" -s "SELECT COUNT(*) FROM loans WHERE status = 'C'"
fork demo --llm anthropic                # the example above on a generated database
```

MCP server (Claude Desktop, Claude Code, Cursor):

```
FORK_DB=/path/to/db.sqlite fork-mcp
```

exposes one tool, `check_sql(question, sql)`, that returns the report as markdown.

## How it works

1. **Parse.** The agent's SQL is parsed with sqlglot and every column reference is resolved to a real table so it can be profiled.
2. **Readings.** Four kinds of dependency are found by heuristics, each with the agent's reading and up to two alternatives:
   - *value literal*: `status = 'C'` where the codes are undocumented, where the literal matches nothing in the column, or where cells hold several values (`keywords = 'Flying'` versus `LIKE '%Flying%'`);
   - *column choice*: the agent picked `balance` where the table also has `current_balance`;
   - *null handling*: `status != 'D'` silently drops NULL rows, `AVG(x)` ignores NULLs, `COUNT(col)` versus `COUNT(*)`;
   - *duplicates*: a list that should perhaps be `DISTINCT`, `COUNT(col)` versus `COUNT(DISTINCT col)`, and `ORDER BY x LIMIT 1` versus every row tied for first.
   One LLM call then decides which alternatives are plausible readings of the *question*, adjusts priors, and adds readings the heuristics cannot see (a code set that means "nonperforming").
3. **Branch.** The query is rewritten once per alternative reading and run. Pass one switches one reading at a time; pass two runs every combination of the forked readings when there are at most 16.
4. **Compare.** Final answers are compared as row multisets with numeric tolerance. A reading that changes the answer is a fork; the rest collapse.
5. **Discriminate.** Each fork gets a query whose result tells the readings apart, in its dumbest working form: the profile of the competing values, how often two columns agree, how many rows are NULL.

## Evaluation on BIRD

BIRD ships an "evidence" hint per question precisely because its columns are ambiguous. The evaluation strips it, runs a simple baseline agent, then runs Fork over the agent's SQL. See `eval/bird/` and `docs/eval.md`; every measurement is logged in `docs/results.md`.

On the full BIRD dev set (1529 usable questions, evidence stripped, baseline agent at 59% accuracy):

| metric | value |
|---|---|
| questions whose answer flips under a plausible alternative reading | 13.5% (207/1529), standard error 0.9 |
| of those flips on right answers, share a judge calls implausible | 29% (human labels on a subsample: 28%) |
| wrong answers where a fork's other branch is the gold answer | 2.7% |

About one in seven questions carries a choice the agent made silently. Ambiguity is a property of the schema, not the agent: on the three databases with coded columns and multi-valued cells (card_games, thrombosis_prediction, california_schools) it is one in four or five, on the cleanest ones one in twenty to fifty. The catch rate is low because it measures agreement with BIRD's gold query, which itself takes arbitrary readings that live in the stripped evidence; the human-labelled forks on wrong answers were mostly judged plausible.

## Scope of v0

In: Postgres and SQLite, Python library, MCP server, CLI, four dependency kinds, one LLM call, dumb discriminating queries, reproducible BIRD evaluation.

Out, deliberately: free-text sources, persisted or versioned readings, dependency tagging of downstream conclusions, research-grade discriminating-query synthesis.

## Development

```
uv venv && uv pip install -e ".[dev]"
pytest
```

MIT.
