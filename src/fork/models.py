"""Data model for Fork.

A *dependency* is a place in the agent's SQL whose meaning is not fixed by the
schema: a literal compared against a column, a column chosen among look-alikes,
or a predicate/aggregate whose treatment of NULLs is a choice.

A *reading* is one way of resolving a dependency. Reading 0 of every dependency
is the agent's own reading (the SQL as written). Alternatives carry a rewrite
that the branch executor applies to the query.

A *fork* is a dependency where at least one alternative reading changes the
final answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

DependencyKind = Literal["value_literal", "column_choice", "null_handling"]


@dataclass
class ColumnProfile:
    table: str
    column: str
    type: str
    row_count: int
    non_null: int
    empty_strings: int
    distinct: int
    top_values: list[tuple[Any, int]]  # (value, count) most frequent first
    numeric: bool

    @property
    def null_rate(self) -> float:
        return 0.0 if self.row_count == 0 else 1 - self.non_null / self.row_count

    @property
    def categorical(self) -> bool:
        return self.distinct <= 50 or (self.row_count > 0 and self.distinct / self.row_count <= 0.02)

    def values(self) -> list[Any]:
        return [v for v, _ in self.top_values]


@dataclass
class Rewrite:
    """A mechanical change to the query AST. Interpreted by fork.rewrite."""

    type: Literal[
        "replace_literal",      # value: new literal
        "literal_set",          # values: list of literals; predicate becomes IN (...)
        "replace_column",       # column: new column name in the same table
        "include_nulls",        # predicate OR column IS NULL
        "empty_as_null",        # IS NULL -> (IS NULL OR = '')
        "coalesce_zero",        # AVG(col) -> AVG(COALESCE(col, 0))
        "count_star",           # COUNT(col) -> COUNT(*)
        "drop_predicate",       # the term does not restrict rows: predicate -> TRUE
        "identity",             # the agent's reading
    ]
    value: Any = None
    values: list[Any] | None = None
    column: str | None = None


@dataclass
class Reading:
    id: str                 # "<dep_id>.<n>"
    description: str        # "status 'C' means closed"
    rewrite: Rewrite
    prior: float            # rough probability this is what the question meant
    source: Literal["agent", "heuristic", "llm"] = "heuristic"


@dataclass
class Dependency:
    id: str                 # "d1"
    kind: DependencyKind
    table: str
    column: str
    node_id: int            # index into root.walk() of the node this hangs off
    detail: str             # human summary: "loans.status = 'C'"
    readings: list[Reading] = field(default_factory=list)
    profile: ColumnProfile | None = None

    @property
    def agent_reading(self) -> Reading:
        return self.readings[0]

    @property
    def alternatives(self) -> list[Reading]:
        return self.readings[1:]


@dataclass
class BranchResult:
    reading_ids: tuple[str, ...]     # one reading id per dependency, in dependency order
    sql: str
    rows: list[tuple] | None
    columns: list[str] | None
    error: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Evidence:
    sql: str
    columns: list[str] | None
    rows: list[tuple] | None
    error: str | None = None
    note: str = ""


@dataclass
class Fork:
    dependency: Dependency
    branches: list[BranchResult]     # one per reading of the dependency, others held at agent reading
    evidence: Evidence | None = None
    open_question: str = ""

    @property
    def answers(self) -> list[tuple[Reading, BranchResult]]:
        return list(zip(self.dependency.readings, self.branches))


@dataclass
class Report:
    question: str
    sql: str
    dialect: str
    base: BranchResult
    dependencies: list[Dependency]
    forks: list[Fork]
    collapsed: list[Dependency]      # dependencies with alternatives that did not change the answer
    untested: list[str]              # reading ids not run (leaf cap)
    interaction_forks: list[BranchResult] = field(default_factory=list)  # combos that differ from base but no single term did
    llm_calls: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def flipped(self) -> bool:
        return bool(self.forks) or bool(self.interaction_forks)

    def to_dict(self) -> dict:
        return asdict(self)
