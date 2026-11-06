import asyncio
import json

import pytest

pytest.importorskip("mcp")


def _text(result) -> str:
    """Tool result text across mcp 1.x (list of content) and 2.x (CallToolResult)."""
    content = getattr(result, "content", result)
    if isinstance(content, tuple):  # mcp 1.x may return (content, structured)
        content = content[0]
    return "".join(getattr(c, "text", "") for c in content)


def test_mcp_tool_roundtrip(loans_path, monkeypatch):
    from fork.mcp_server import build_server

    monkeypatch.setenv("FORK_DB", loans_path)
    server = build_server()
    tools = asyncio.run(server.list_tools())
    assert [t.name for t in tools] == ["check_sql"]
    result = asyncio.run(server.call_tool("check_sql", {"question": "How many closed loans?", "sql": "SELECT COUNT(*) FROM loans WHERE status = 'C'"}))
    text = _text(result)
    assert "Fork 1" in text and "12" in text


def test_mcp_tool_json(loans_path):
    from fork.mcp_server import build_server

    server = build_server()
    result = asyncio.run(server.call_tool("check_sql", {"question": "q", "sql": "SELECT COUNT(*) FROM loans WHERE status = 'C'", "db": loans_path, "format": "json"}))
    data = json.loads(_text(result))
    assert data["flipped"] is True and data["base"]["answer"] == "12"
