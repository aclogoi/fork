"""Step 2: for each dependency, propose two or three readings with a rough prior.

Heuristics run first and are cheap. They produce the dependency list and the
high-confidence alternatives (a literal that matches nothing in the column, a
look-alike column, a NULL-sensitive predicate). The LLM then sees the question,
the SQL, and the profiles, and decides which alternatives are plausible readings
of the *question*, adjusting priors and adding readings the heuristics cannot
see (a code set that means "nonperforming").
"""

from __future__ import annotations

import difflib
from typing import Any

from .db import Database
from .models import ColumnProfile, Dependency, Reading, Rewrite
from .parse import (
    ParsedQuery,
    column_sites,
    literal_sites,
    name_tokens,
    null_sites,
)

MAX_ALTERNATIVES_PER_DEP = 2
NUMERIC_TYPES = ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE", "MONEY", "SERIAL")
DATE_TYPES = ("DATE", "TIME")


def type_class(declared: str, profile: ColumnProfile | None) -> str:
    d = (declared or "").upper()
    if any(k in d for k in DATE_TYPES):
        return "date"
    if any(k in d for k in NUMERIC_TYPES):
        return "number"
    if profile is not None and profile.numeric:
        return "number"
    return "text"


def heuristic_dependencies(pq: ParsedQuery, db: Database) -> list[Dependency]:
    deps: list[Dependency] = []
    n = 0

    def new_id():
        nonlocal n
        n += 1
        return f"d{n}"

    # -- value literals
    for site in literal_sites(pq, db):
        prof = db.profile(site.table, site.column)
        if prof is None or not prof.categorical:
            continue
        lits = ", ".join(repr(v) for v in site.values)
        detail = f"{site.table}.{site.column} {site.op} {lits}"
        dep = Dependency(new_id(), "value_literal", site.table, site.column, site.node_id, detail, profile=prof)
        agent_desc = f"{site.column} {site.op} {lits} as written"
        dep.readings.append(Reading(f"{dep.id}.0", agent_desc, Rewrite("identity"), 0.6, "agent"))
        for alt in _literal_alternatives(site.values, site.op, prof):
            alt.id = f"{dep.id}.{len(dep.readings)}"
            dep.readings.append(alt)
        deps.append(dep)

    # -- column choice
    used = {(s.table.lower(), s.column.lower()) for s in column_sites(pq, db)}
    for site in column_sites(pq, db):
        if site.join_only:
            continue
        table = db.find_table(site.table)
        if table is None:
            continue
        if site.column in table.primary_key or any(site.column == fk[0] for fk in table.foreign_keys):
            continue
        prof = db.profile(site.table, site.column)
        tc = type_class(table.type_of(site.column), prof)
        cands = []
        for other, oty in table.columns:
            if other.lower() == site.column.lower() or (site.table.lower(), other.lower()) in used:
                continue
            if other in table.primary_key or any(other == fk[0] for fk in table.foreign_keys):
                continue
            sim = _name_similarity(site.column, other)
            if sim < 0.34:
                continue
            if type_class(oty, None) != tc and tc != "text":
                continue
            cands.append((sim, other))
        if not cands:
            continue
        cands.sort(reverse=True)
        dep = Dependency(new_id(), "column_choice", site.table, site.column, site.node_ids[0],
                         f"{site.table}.{site.column} (vs {', '.join(c for _, c in cands[:3])})", profile=prof)
        dep.readings.append(Reading(f"{dep.id}.0", f"the question means {site.table}.{site.column}", Rewrite("identity"), 0.7, "agent"))
        for sim, other in cands[:MAX_ALTERNATIVES_PER_DEP]:
            dep.readings.append(Reading(
                f"{dep.id}.{len(dep.readings)}", f"the question means {site.table}.{other}",
                Rewrite("replace_column", column=other), round(0.15 + 0.15 * sim, 2), "heuristic"))
        deps.append(dep)

    # -- null handling
    for site in null_sites(pq, db):
        prof = db.profile(site.table, site.column)
        if prof is None:
            continue
        rw, desc_agent, desc_alt, prior = _null_alternative(site.shape, prof, site.column)
        if rw is None:
            continue
        dep = Dependency(new_id(), "null_handling", site.table, site.column, site.node_id,
                         f"{site.table}.{site.column} {site.shape} (nulls {prof.null_rate:.0%}, empty {prof.empty_strings})", profile=prof)
        dep.readings.append(Reading(f"{dep.id}.0", desc_agent, Rewrite("identity"), 1 - prior, "agent"))
        dep.readings.append(Reading(f"{dep.id}.1", desc_alt, rw, prior, "heuristic"))
        deps.append(dep)

    return deps


# ---- literal alternatives -------------------------------------------------


def _norm(v: Any) -> str:
    return str(v).strip().lower()


def _literal_alternatives(values: list[str], op: str, prof: ColumnProfile) -> list[Reading]:
    present = {str(v): v for v in prof.values()}
    out: list[Reading] = []
    missing = [v for v in values if v not in present]
    if missing:
        # The literal matches nothing: the agent's reading selects no rows. Find what it meant.
        matches: list[tuple[float, Any]] = []
        for lit in missing:
            for cand in prof.values():
                s = _value_similarity(lit, str(cand))
                if s >= 0.6:
                    matches.append((s, cand))
        matches.sort(key=lambda x: -x[0])
        seen = set()
        for s, cand in matches:
            if cand in seen:
                continue
            seen.add(cand)
            fixed = [present.get(v, v) if v in present else cand for v in values]
            if len(fixed) == 1:
                rw = Rewrite("replace_literal", value=fixed[0])
            else:
                rw = Rewrite("literal_set", values=fixed)
            out.append(Reading("", f"{missing[0]!r} means {cand!r} (literal not in column)", rw, round(0.3 + 0.3 * s, 2), "heuristic"))
            if len(out) >= MAX_ALTERNATIVES_PER_DEP:
                break
        return out
    # Literal is present. Case/whitespace variants are the same code.
    variants = []
    for v in values:
        for cand in prof.values():
            if cand != v and _norm(cand) == _norm(v):
                variants.append(cand)
    if variants:
        allv = list(dict.fromkeys(list(values) + variants))
        out.append(Reading("", f"{values[0]!r} and {variants[0]!r} are the same value", Rewrite("literal_set", values=allv), 0.3, "heuristic"))
    return out


def _value_similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 0.8
    if len(na) <= 2 or len(nb) <= 2:
        # short code vs word: initial letter match
        return 0.6 if na[0] == nb[0] and (len(na) <= 2) != (len(nb) <= 2) else 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _name_similarity(a: str, b: str) -> float:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    inter = len(ta & tb)
    if inter == 0:
        # character-level fallback for abbreviations (amt vs amount)
        r = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        return r if r >= 0.75 else 0.0
    if ta <= tb or tb <= ta:
        return 0.7
    return inter / len(ta | tb)


def _null_alternative(shape: str, prof: ColumnProfile, column: str):
    nulls = prof.null_rate > 0
    empties = prof.empty_strings > 0
    if shape == "neq" and nulls:
        return Rewrite("include_nulls"), f"rows with NULL {column} are excluded", f"rows with NULL {column} count as not matching", 0.3
    if shape == "not_in" and nulls:
        return Rewrite("include_nulls"), f"rows with NULL {column} are excluded", f"rows with NULL {column} count as not in the set", 0.3
    if shape == "is_null" and empties:
        return Rewrite("empty_as_null"), f"only NULL {column} counts as missing", f"empty-string {column} also counts as missing", 0.35
    if shape == "is_not_null" and empties:
        return Rewrite("empty_as_null"), f"empty-string {column} counts as present", f"empty-string {column} counts as missing", 0.35
    if shape == "avg" and nulls:
        return Rewrite("coalesce_zero"), f"AVG ignores NULL {column}", f"NULL {column} counts as 0 in the average", 0.2
    if shape == "count" and nulls:
        return Rewrite("count_star"), f"COUNT skips rows with NULL {column}", f"every row counts, NULL {column} included", 0.25
    return None, "", "", 0.0


# ---- LLM pass ---------------------------------------------------------------

LLM_SYSTEM = """You review a SQL query that an agent wrote to answer a natural-language question against a database with undocumented columns. Your job is to find the places where the agent silently chose one reading of a column or value when another reading is also a plausible interpretation of the question.

You are given candidate dependencies found by heuristics, each with a column profile (sample values, null rate) and candidate alternative readings. For each dependency decide which alternatives a careful analyst could plausibly take the question to mean. Be strict: an alternative is plausible only if, reading the question and looking at the data, a reasonable person might resolve the term that way. Do not propose alternatives that merely change the query; propose readings of the question.

You may also add alternatives the heuristics missed, restricted to these rewrite types:
- replace_literal: the literal should be a different value from the column (value must appear in the profile)
- literal_set: the question's term covers several values from the column (all must appear in the profile)
- replace_column: the question's term refers to a different column of the same table (name it exactly as in the schema)
And you may add new column_choice dependencies for columns the query uses when a sibling column in the same table is a plausible referent of the question's wording.

Priors are rough probabilities that the question meant that reading; they need not sum to one. Keep at most 2 alternatives per dependency. Drop every dependency whose agent reading is clearly the only plausible one by returning it with an empty alternatives list."""

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "agent_prior": {"type": "number"},
                    "alternatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "prior": {"type": "number"},
                                "rewrite_type": {"type": "string", "enum": ["replace_literal", "literal_set", "replace_column", "include_nulls", "empty_as_null", "coalesce_zero", "count_star"]},
                                "value": {"type": ["string", "number", "null"]},
                                "values": {"type": ["array", "null"], "items": {"type": ["string", "number"]}},
                                "column": {"type": ["string", "null"]},
                            },
                            "required": ["description", "prior", "rewrite_type", "value", "values", "column"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "agent_prior", "alternatives"],
                "additionalProperties": False,
            },
        },
        "new_column_choices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "column": {"type": "string"},
                    "alternative_column": {"type": "string"},
                    "description": {"type": "string"},
                    "prior": {"type": "number"},
                },
                "required": ["table", "column", "alternative_column", "description", "prior"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["dependencies", "new_column_choices"],
    "additionalProperties": False,
}


def _profile_text(p: ColumnProfile | None) -> str:
    if p is None:
        return "no profile"
    vals = ", ".join(f"{v!r}×{c}" for v, c in p.top_values[:25])
    more = f", … {p.distinct} distinct" if p.distinct > 25 else ""
    return f"type={p.type or '?'} rows={p.row_count} nulls={p.null_rate:.0%} empty={p.empty_strings} values: {vals}{more}"


def llm_prompt(question: str, sql: str, deps: list[Dependency], pq: ParsedQuery, db: Database) -> str:
    tables = sorted(set(pq.aliases.values()))
    schema_lines = []
    for t in tables:
        tab = db.find_table(t)
        if tab:
            schema_lines.append(f"{t}: " + ", ".join(f"{c} {ty}" for c, ty in tab.columns))
    lines = [f"Question: {question}", f"Agent SQL: {sql}", "", "Tables used:", *schema_lines, "", "Candidate dependencies:"]
    for d in deps:
        lines.append(f"- {d.id} [{d.kind}] {d.detail}")
        lines.append(f"    profile: {_profile_text(d.profile)}")
        lines.append(f"    agent reading: {d.agent_reading.description}")
        for r in d.alternatives:
            lines.append(f"    heuristic alternative: {r.description} (rewrite {r.rewrite.type} {r.rewrite.value or r.rewrite.values or r.rewrite.column})")
    return "\n".join(lines)


def merge_llm_readings(deps: list[Dependency], answer: dict, pq: ParsedQuery, db: Database) -> list[Dependency]:
    """Apply the LLM's verdicts: keep the dependencies it kept, adopt its alternatives and priors."""
    by_id = {d.id: d for d in deps}
    out: list[Dependency] = []
    for item in answer.get("dependencies", []):
        d = by_id.get(item.get("id"))
        if d is None:
            continue
        alts = []
        for a in item.get("alternatives", [])[:MAX_ALTERNATIVES_PER_DEP]:
            rw = _rewrite_from_llm(a, d, db)
            if rw is None:
                continue
            alts.append(Reading("", a.get("description", ""), rw, float(a.get("prior", 0.3)), "llm"))
        if not alts:
            continue
        agent = d.readings[0]
        agent.prior = float(item.get("agent_prior", agent.prior))
        d.readings = [agent]
        for a in alts:
            a.id = f"{d.id}.{len(d.readings)}"
            d.readings.append(a)
        out.append(d)
    n = len(deps)
    for item in answer.get("new_column_choices", []):
        site = _find_column_site(item.get("table", ""), item.get("column", ""), pq, db)
        if site is None:
            continue
        table = db.find_table(site[0])
        alt = db.find_column(site[0], item.get("alternative_column", ""))
        if table is None or alt is None or alt.lower() == site[1].lower():
            continue
        if any(o.kind == "column_choice" and o.table == site[0] and o.column == site[1] for o in out):
            continue
        n += 1
        d = Dependency(f"d{n}", "column_choice", site[0], site[1], site[2], f"{site[0]}.{site[1]} (vs {alt})", profile=db.profile(site[0], site[1]))
        d.readings.append(Reading(f"{d.id}.0", f"the question means {site[0]}.{site[1]}", Rewrite("identity"), 0.6, "agent"))
        d.readings.append(Reading(f"{d.id}.1", item.get("description") or f"the question means {site[0]}.{alt}", Rewrite("replace_column", column=alt), float(item.get("prior", 0.3)), "llm"))
        out.append(d)
    return out


def _rewrite_from_llm(a: dict, d: Dependency, db: Database) -> Rewrite | None:
    t = a.get("rewrite_type")
    prof = d.profile
    values = prof.values() if prof else []

    def canon(v):
        """Map the model's literal onto a value that exists in the column: exact first, then case-insensitive."""
        if v in values:
            return v
        sv = str(v)
        for cand in values:
            if str(cand) == sv:
                return cand
        for cand in values:
            if str(cand).strip().lower() == sv.strip().lower():
                return cand
        return v

    if t == "replace_literal":
        v = a.get("value")
        if v is None:
            return None
        return Rewrite("replace_literal", value=canon(v))
    if t == "literal_set":
        vals = a.get("values") or []
        if not vals:
            return None
        return Rewrite("literal_set", values=[canon(v) for v in vals])
    if t == "replace_column":
        c = a.get("column")
        real = db.find_column(d.table, c or "")
        if real is None or real.lower() == d.column.lower():
            return None
        return Rewrite("replace_column", column=real)
    if t in ("include_nulls", "empty_as_null", "coalesce_zero", "count_star"):
        if d.kind != "null_handling":
            return None
        return Rewrite(t)
    return None


def _find_column_site(table: str, column: str, pq: ParsedQuery, db: Database):
    for site in column_sites(pq, db):
        if site.table.lower() == table.lower() and site.column.lower() == column.lower():
            return site.table, site.column, site.node_ids[0]
    return None
