"""Render a Report as text, markdown, or a JSON-safe dict."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .compare import summarize
from .models import Report


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


def to_dict(report: Report) -> dict:
    d = asdict(report)
    # trim row payloads so the dict stays small
    for key in ("base",):
        d[key]["answer"] = summarize(report.base.rows, report.base.columns)
    d["flipped"] = report.flipped
    for f, fd in zip(report.forks, d["forks"], strict=False):
        fd["answers"] = [
            {"reading": r.id, "description": r.description, "prior": r.prior, "source": r.source,
             "answer": summarize(b.rows, b.columns), "error": b.error, "sql": b.sql}
            for r, b in f.answers
        ]
    return _jsonable(d)


def to_json(report: Report, indent: int | None = 2) -> str:
    return json.dumps(to_dict(report), indent=indent)


def to_text(report: Report, show_sql: bool = False) -> str:
    out = []
    out.append(f"Question: {report.question}")
    out.append(f"Agent answer: {summarize(report.base.rows, report.base.columns)}" + (f"  [error: {report.base.error}]" if report.base.error else ""))
    out.append("")
    if not report.forks and not report.interaction_forks:
        out.append("No forks: the answer is the same under every plausible alternative reading checked.")
    for i, f in enumerate(report.forks, 1):
        d = f.dependency
        out.append(f"FORK {i}: {d.detail}  [{d.kind.replace('_', ' ')}]")
        for r, b in f.answers:
            tag = "agent" if r.source == "agent" else r.source
            ans = summarize(b.rows, b.columns) if b.ok else f"error: {b.error}"
            out.append(f"  {ans:<40} if {r.description}  (prior {r.prior:.2f}, {tag})")
            if show_sql and b.sql:
                out.append(f"      {b.sql}")
        if f.open_question:
            out.append(f"  Open: {f.open_question}")
        if f.evidence and f.evidence.sql:
            out.append(f"  Evidence: {f.evidence.sql}")
            if f.evidence.rows is not None:
                hdr = " | ".join(f.evidence.columns or [])
                out.append(f"    {hdr}")
                for row in f.evidence.rows[:10]:
                    out.append("    " + " | ".join(_fmt(x) for x in row))
            elif f.evidence.error:
                out.append(f"    (evidence query failed: {f.evidence.error})")
        out.append("")
    if report.interaction_forks:
        out.append(f"Interaction forks: {len(report.interaction_forks)} reading combinations change the answer only together:")
        for b in report.interaction_forks[:6]:
            out.append(f"  {summarize(b.rows, b.columns):<40} under {', '.join(b.reading_ids)}")
        out.append("")
    if report.collapsed:
        out.append("Checked, answer unchanged: " + "; ".join(d.detail for d in report.collapsed))
    if report.untested:
        out.append(f"Untested (branch cap): {', '.join(report.untested)}")
    for n in report.notes:
        out.append(f"Note: {n}")
    return "\n".join(out).rstrip() + "\n"


def _fmt(x: Any) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def to_markdown(report: Report) -> str:
    out = [f"**Question:** {report.question}", "", f"**Agent answer:** `{summarize(report.base.rows, report.base.columns)}`", ""]
    if not report.forks:
        out.append("_No forks: the answer holds under every plausible alternative reading checked._")
    for i, f in enumerate(report.forks, 1):
        d = f.dependency
        out.append(f"### Fork {i}: `{d.detail}`")
        out.append("")
        out.append("| answer | reading | prior | source |")
        out.append("|---|---|---|---|")
        for r, b in f.answers:
            ans = summarize(b.rows, b.columns) if b.ok else f"error: {b.error}"
            out.append(f"| `{ans}` | {r.description} | {r.prior:.2f} | {r.source} |")
        out.append("")
        if f.open_question:
            out.append(f"**Open:** {f.open_question}")
        if f.evidence and f.evidence.sql:
            out.append("")
            out.append("```sql\n" + f.evidence.sql + "\n```")
            if f.evidence.rows:
                out.append("| " + " | ".join(f.evidence.columns or []) + " |")
                out.append("|" + "---|" * len(f.evidence.columns or []))
                for row in f.evidence.rows[:10]:
                    out.append("| " + " | ".join(_fmt(x) for x in row) + " |")
        out.append("")
    if report.collapsed:
        out.append("_Checked, unchanged:_ " + "; ".join(f"`{d.detail}`" for d in report.collapsed))
    return "\n".join(out).rstrip() + "\n"
