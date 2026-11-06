"""Command line: `fork check`, `fork demo`."""

from __future__ import annotations

import os
import sys

import click

from . import check as _check
from .report import to_json, to_markdown, to_text


def _load_env():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


@click.group()
def main():
    """Fork: find where a text-to-SQL answer depends on an unstated reading."""
    _load_env()


@main.command("check")
@click.option("--db", "db_target", required=True, help="SQLite file path, sqlite:// or postgresql:// URL.")
@click.option("--question", "-q", required=True, help="The natural-language question the agent answered.")
@click.option("--sql", "-s", required=False, help="The agent's SQL. Reads stdin if omitted.")
@click.option("--llm", default=None, type=click.Choice(["anthropic", "openai", "none"]), help="Reading proposer. Default: from env.")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "markdown", "json"]))
@click.option("--no-evidence", is_flag=True, help="Skip discriminating queries.")
@click.option("--show-sql", is_flag=True, help="Print the SQL of every branch.")
@click.option("--timeout", default=30.0, show_default=True, help="Per-branch query timeout in seconds.")
def check_cmd(db_target, question, sql, llm, fmt, no_evidence, show_sql, timeout):
    """Check one question + SQL against a database."""
    if not sql:
        sql = sys.stdin.read()
    report = _check(question, sql, db_target, llm=llm, evidence=not no_evidence, timeout=timeout)
    if fmt == "json":
        click.echo(to_json(report))
    elif fmt == "markdown":
        click.echo(to_markdown(report))
    else:
        click.echo(to_text(report, show_sql=show_sql))
    sys.exit(2 if report.flipped else 0)


@main.command("demo")
@click.option("--llm", default="none", type=click.Choice(["anthropic", "openai", "none"]))
def demo(llm):
    """Build the loans demo database in a temp file and run the 12-vs-31 check."""
    import sqlite3
    import tempfile

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests"))
    try:
        from conftest import build_loans
    except ImportError:
        click.echo("demo needs the repository's tests/ directory", err=True)
        sys.exit(1)
    path = os.path.join(tempfile.mkdtemp(), "loans.sqlite")
    conn = sqlite3.connect(path)
    build_loans(conn)
    conn.close()
    question = "How many nonperforming loans are there?"
    sql = "SELECT COUNT(*) FROM loans WHERE status = 'C'"
    click.echo(f"db: {path}\nquestion: {question}\nsql: {sql}\n")
    report = _check(question, sql, path, llm=llm)
    click.echo(to_text(report))


if __name__ == "__main__":
    main()
