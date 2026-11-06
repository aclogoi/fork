"""MCP server exposing fork.check as a tool.

Run with `fork-mcp` (stdio). Configure the database once with FORK_DB or pass
`db` per call. Any MCP client (Claude Desktop, Claude Code, Cursor) can then
ask Fork to check a query before trusting the number.
"""

from __future__ import annotations

import os
from typing import Any

from . import check as _check
from .report import to_dict, to_markdown


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer

    server = MCPServer("fork", instructions=(
        "Fork checks a SQL query that answers a natural-language question for readings the answer "
        "silently depends on. Call check_sql after generating SQL and before reporting a number; "
        "if it returns forks, show the user the competing answers and the open question."
    ))

    @server.tool()
    def check_sql(question: str, sql: str, db: str | None = None, evidence: bool = True, format: str = "markdown") -> Any:
        """Check a text-to-SQL answer for ambiguity.

        question: the user's natural-language question.
        sql: the SQL generated to answer it.
        db: SQLite path or postgresql:// URL. Defaults to the FORK_DB environment variable.
        evidence: also run a discriminating query per fork.
        format: "markdown" (default) or "json".
        """
        target = db or os.environ.get("FORK_DB")
        if not target:
            raise ValueError("No database: pass db= or set FORK_DB")
        report = _check(question, sql, target, evidence=evidence)
        if format == "json":
            return to_dict(report)
        return to_markdown(report)

    return server


def main():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    build_server().run()


if __name__ == "__main__":
    main()
