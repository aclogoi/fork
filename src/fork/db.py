"""Database adapter: SQLite and Postgres behind one small interface.

Everything Fork needs from a database: dialect name, schema, column profiles,
and running a query with a timeout and a row cap.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from .models import ColumnProfile


def _qtable(name: str) -> exp.Table:
    """A table reference with the identifier always quoted (BIRD has a table called `order`)."""
    return exp.Table(this=exp.to_identifier(name, quoted=True))


def _qcol(name: str) -> exp.Column:
    return exp.Column(this=exp.to_identifier(name, quoted=True))


@dataclass
class Table:
    name: str
    columns: list[tuple[str, str]]  # (name, declared type)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)  # (col, ref_table, ref_col)

    def column_names(self) -> list[str]:
        return [c for c, _ in self.columns]

    def type_of(self, column: str) -> str:
        for c, t in self.columns:
            if c.lower() == column.lower():
                return t
        return ""


class QueryTimeout(Exception):
    pass


class Database:
    """Wraps a DB-API connection. Build with Database.connect()."""

    def __init__(self, conn: Any, dialect: str):
        self.conn = conn
        self.dialect = dialect
        self._schema: dict[str, Table] | None = None
        self._profiles: dict[tuple[str, str], ColumnProfile] = {}

    # ---- construction -------------------------------------------------

    @classmethod
    def connect(cls, target: Any) -> Database:
        if isinstance(target, Database):
            return target
        if isinstance(target, sqlite3.Connection):
            return cls(target, "sqlite")
        if isinstance(target, str):
            if target.startswith("sqlite://"):
                path = target[len("sqlite://"):]
                if path.startswith("/") and not os.path.exists(path) and os.path.exists(path[1:]):
                    path = path[1:]
                return cls(sqlite3.connect(path), "sqlite")
            if target.startswith(("postgres://", "postgresql://")):
                import psycopg  # optional dependency

                conn = psycopg.connect(target, autocommit=True)
                return cls(conn, "postgres")
            if os.path.exists(target):
                return cls(sqlite3.connect(target), "sqlite")
            raise ValueError(f"Cannot interpret database target: {target!r}")
        mod = type(target).__module__
        if mod.startswith("psycopg"):
            return cls(target, "postgres")
        raise ValueError(f"Unsupported connection object: {type(target)!r}")

    # ---- execution ----------------------------------------------------

    def execute(
        self, sql: str, timeout: float = 30.0, max_rows: int = 10_000
    ) -> tuple[list[str], list[tuple], bool]:
        """Run sql. Returns (column names, rows, truncated). Raises on error."""
        if self.dialect == "sqlite":
            return self._execute_sqlite(sql, timeout, max_rows)
        return self._execute_pg(sql, timeout, max_rows)

    def _execute_sqlite(self, sql, timeout, max_rows):
        conn: sqlite3.Connection = self.conn
        deadline = time.monotonic() + timeout

        def guard():
            if time.monotonic() > deadline:
                return 1  # non-zero aborts
            return 0

        conn.set_progress_handler(guard, 10_000)
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            return cols, [tuple(r) for r in rows[:max_rows]], truncated
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                raise QueryTimeout(f"query exceeded {timeout}s") from e
            raise
        finally:
            conn.set_progress_handler(None, 0)

    def _execute_pg(self, sql, timeout, max_rows):
        conn = self.conn
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(timeout * 1000)}")
            try:
                cur.execute(sql)
            except Exception as e:  # psycopg.errors.QueryCanceled
                if type(e).__name__ == "QueryCanceled":
                    raise QueryTimeout(f"query exceeded {timeout}s") from e
                raise
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
            truncated = len(rows) > max_rows
            return cols, [tuple(r) for r in rows[:max_rows]], truncated

    def scalar(self, sql: str) -> Any:
        _, rows, _ = self.execute(sql)
        return rows[0][0] if rows else None

    # ---- schema -------------------------------------------------------

    def schema(self) -> dict[str, Table]:
        if self._schema is None:
            self._schema = self._load_sqlite_schema() if self.dialect == "sqlite" else self._load_pg_schema()
        return self._schema

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        """Schema in the shape sqlglot's optimizer wants: {table: {column: type}}."""
        out = {}
        for t in self.schema().values():
            out[t.name] = {c: (ty or "TEXT") for c, ty in t.columns}
        return out

    def _load_sqlite_schema(self) -> dict[str, Table]:
        conn = self.conn
        tables: dict[str, Table] = {}
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
        )]
        for name in names:
            q = name.replace('"', '""')
            cols, pk = [], []
            for cid, cname, ctype, notnull, dflt, ispk in conn.execute(f'PRAGMA table_info("{q}")'):
                cols.append((cname, ctype or ""))
                if ispk:
                    pk.append(cname)
            fks = [(r[3], r[2], r[4] or "") for r in conn.execute(f'PRAGMA foreign_key_list("{q}")')]
            tables[name] = Table(name, cols, pk, fks)
        return tables

    def _load_pg_schema(self) -> dict[str, Table]:
        tables: dict[str, Table] = {}
        _, rows, _ = self.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() ORDER BY table_name, ordinal_position"
        )
        for t, c, ty in rows:
            tables.setdefault(t, Table(t, [])).columns.append((c, ty))
        _, pks, _ = self.execute(
            "SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
            "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = current_schema()"
        )
        for t, c in pks:
            if t in tables:
                tables[t].primary_key.append(c)
        return tables

    def find_table(self, name: str) -> Table | None:
        s = self.schema()
        if name in s:
            return s[name]
        for k, v in s.items():
            if k.lower() == name.lower():
                return v
        return None

    def find_column(self, table: str, column: str) -> str | None:
        t = self.find_table(table)
        if not t:
            return None
        for c, _ in t.columns:
            if c == column:
                return c
        for c, _ in t.columns:
            if c.lower() == column.lower():
                return c
        return None

    # ---- profiling ----------------------------------------------------

    def profile(self, table: str, column: str, top_n: int = 50) -> ColumnProfile | None:
        key = (table.lower(), column.lower())
        if key in self._profiles:
            return self._profiles[key]
        t = self.find_table(table)
        col = self.find_column(table, column)
        if not t or not col:
            return None
        tbl = _qtable(t.name)
        c = _qcol(col)
        d = self.dialect
        try:
            stats_sql = (
                exp.select(
                    exp.Count(this=exp.Star()).as_("n"),
                    exp.Count(this=c).as_("nn"),
                    exp.Sum(this=exp.Case().when(exp.EQ(this=c, expression=exp.Literal.string("")), exp.Literal.number(1)).else_(exp.Literal.number(0))).as_("empties"),
                    exp.Count(this=exp.Distinct(expressions=[c])).as_("nd"),
                )
                .from_(tbl)
                .sql(dialect=d)
            )
            _, rows, _ = self.execute(stats_sql, timeout=60)
            n, nn, empties, nd = rows[0]
            top_sql = (
                exp.select(c, exp.Count(this=exp.Star()).as_("cnt"))
                .from_(tbl)
                .where(exp.Not(this=exp.Is(this=c, expression=exp.Null())))
                .group_by(c)
                .order_by(exp.Ordered(this=exp.column("cnt"), desc=True))
                .limit(top_n)
                .sql(dialect=d)
            )
            _, top, _ = self.execute(top_sql, timeout=60)
        except Exception:
            return None
        decl = t.type_of(col).upper()
        numeric = any(k in decl for k in ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE")) or (
            bool(top) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v, _ in top)
        )
        prof = ColumnProfile(
            table=t.name, column=col, type=decl, row_count=int(n or 0), non_null=int(nn or 0),
            empty_strings=int(empties or 0), distinct=int(nd or 0),
            top_values=[(v, int(k)) for v, k in top], numeric=numeric,
        )
        self._profiles[key] = prof
        return prof

    def sample_rows(self, table: str, n: int = 3) -> tuple[list[str], list[tuple]]:
        t = self.find_table(table)
        if not t:
            return [], []
        sql = exp.select("*").from_(_qtable(t.name)).limit(n).sql(dialect=self.dialect)
        cols, rows, _ = self.execute(sql, timeout=30)
        return cols, rows

    def ddl(self, tables: list[str] | None = None) -> str:
        """CREATE TABLE text for prompting. SQLite: the stored DDL; Postgres: synthesized."""
        out = []
        wanted = None if tables is None else {x.lower() for x in tables}
        if self.dialect == "sqlite":
            for name, sql in self.conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ):
                if wanted is None or name.lower() in wanted:
                    out.append(sql.strip() + ";")
            return "\n\n".join(out)
        for t in self.schema().values():
            if wanted is not None and t.name.lower() not in wanted:
                continue
            cols = ",\n  ".join(f'"{c}" {ty}' for c, ty in t.columns)
            out.append(f'CREATE TABLE "{t.name}" (\n  {cols}\n);')
        return "\n\n".join(out)
