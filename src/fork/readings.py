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

from sqlglot import exp

from .db import Database, _qcol, _qtable
from .models import ColumnProfile, Dependency, Reading, Rewrite
from .parse import (
    DupSite,
    ParsedQuery,
    column_sites,
    duplicate_sites,
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
        if prof is None:
            continue
        if site.op == "like":
            # LIKE '%v%': the agent matched any cell containing v. Is v also a whole value?
            v = site.values[0]
            if not _count_where(db, site.table, site.column, exp.EQ(this=_qcol(site.column), expression=exp.Literal.string(v))):
                continue
            dep = Dependency(new_id(), "value_literal", site.table, site.column, site.node_id,
                             f"{site.table}.{site.column} LIKE '%{v}%'", profile=prof)
            dep.readings.append(Reading(f"{dep.id}.0", f"{site.column} contains {v!r} anywhere in the cell (LIKE)", Rewrite("identity"), 0.6, "agent"))
            dep.readings.append(Reading(f"{dep.id}.1", f"{site.column} is exactly {v!r}", Rewrite("exact_match"), 0.3, "heuristic"))
            deps.append(dep)
            continue
        contains = 0
        if site.op == "=" and not site.negated and len(site.values) == 1:
            contains = _contains_count(db, site.table, site.column, site.values[0])
        if not prof.categorical and not contains:
            continue
        lits = ", ".join(repr(v) for v in site.values)
        detail = f"{site.table}.{site.column} {site.op} {lits}"
        dep = Dependency(new_id(), "value_literal", site.table, site.column, site.node_id, detail, profile=prof)
        agent_desc = f"{site.column} {site.op} {lits} as written"
        dep.readings.append(Reading(f"{dep.id}.0", agent_desc, Rewrite("identity"), 0.6, "agent"))
        for alt in _literal_alternatives(site.values, site.op, prof):
            alt.id = f"{dep.id}.{len(dep.readings)}"
            dep.readings.append(alt)
        if contains:
            dep.readings.append(Reading(f"{dep.id}.{len(dep.readings)}",
                                        f"{site.values[0]!r} means any {site.column} that includes {site.values[0]!r} ({contains} more rows hold it with other values)",
                                        Rewrite("like_contains"), 0.3, "heuristic"))
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
            otc = type_class(oty, None)
            sim = _name_similarity(site.column, other)
            if tc == "date" and otc == "date":
                sim = max(sim, 0.34)  # two date columns in one table are always a choice
            if sim < 0.34:
                continue
            if otc != tc and tc != "text":
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

    # -- duplicates
    for site in duplicate_sites(pq, db):
        prof = db.profile(site.table, site.column) if site.column else None
        rw, desc_agent, desc_alt, prior = _dup_alternative(site)
        dep = Dependency(new_id(), "duplicates", site.table, site.column, site.node_id, site.detail, profile=prof)
        dep.readings.append(Reading(f"{dep.id}.0", desc_agent, Rewrite("identity"), 1 - prior, "agent"))
        dep.readings.append(Reading(f"{dep.id}.1", desc_alt, rw, prior, "heuristic"))
        deps.append(dep)

    return deps


def _count_where(db: Database, table: str, column: str, cond: exp.Expression, timeout: float = 5.0) -> int:
    q = exp.select(exp.Count(this=exp.Star())).from_(_qtable(table)).where(cond)
    try:
        _, rows, _ = db.execute(q.sql(dialect=db.dialect), timeout=timeout, max_rows=1)
        return int(rows[0][0]) if rows else 0
    except Exception:
        return 0


def _contains_count(db: Database, table: str, column: str, value: str) -> int:
    """Rows whose cell holds `value` alongside other content ('Flying, Trample' for 'Flying')."""
    if not value or "%" in value or "_" in value or len(value) < 2:
        return 0
    col = _qcol(column)
    cond = exp.and_(
        exp.Like(this=col, expression=exp.Literal.string(f"%{value}%")),
        exp.NEQ(this=col.copy(), expression=exp.Literal.string(value)),
    )
    return _count_where(db, table, column, cond)


def _dup_alternative(site: DupSite) -> tuple[Rewrite, str, str, float]:
    c = site.column or "value"
    if site.shape == "select_plain":
        return Rewrite("add_distinct"), "one row per matching record, repeats kept", "one row per distinct value (DISTINCT)", 0.3
    if site.shape == "count_distinct":
        return Rewrite("count_all"), f"the number of distinct {c} values", f"the number of records with a {c}", 0.25
    if site.shape == "count_plain":
        return Rewrite("count_distinct"), f"the number of records with a {c} (repeats counted)", f"the number of distinct {c} values", 0.3
    return Rewrite("include_ties"), "the single first row in order (ties broken arbitrarily)", "every row tied for first", 0.3


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

You are given candidate dependencies found by heuristics, each with a column profile (sample values, null rate) and candidate alternative readings. For each dependency decide which alternatives a careful analyst could plausibly take the question to mean. Be strict: an alternative is plausible only if it is a defensible interpretation of the question's wording, given the data. When the question's wording names the agent's column or value directly (it says 'K-12' and the agent used the K-12 column; it says 'free' and the agent used the free-only column), the look-alike column is not a reading, it is a different question: do not list it. Do not propose alternatives that merely change the query; propose readings of the question.

You may also add alternatives the heuristics missed, restricted to these rewrite types:
- replace_literal: the literal should be a different value from the column (value must appear in the profile)
- literal_set: the question's term covers several values from the column (all must appear in the profile)
- replace_column: the question's term refers to a different column of the same table (name it exactly as in the schema)
- drop_predicate: the question's term does not restrict rows at all; the agent turned a descriptive phrase into a filter the question did not ask for, or filtered on a code when the question meant every row
- like_contains: the cell holds several values ('Flying, Trample'); the question's term matches any cell that includes it, not only cells equal to it
- exact_match: the reverse: the agent used LIKE '%term%' but the question means cells equal to the term
And you may add new column_choice dependencies for columns the query uses when a sibling column in the same table is a plausible referent of the question's wording.

Dependencies of kind "duplicates" are about repeats, not columns: whether the question wants one row per distinct value or one per matching record (DISTINCT or not), whether "how many X" counts records or distinct values, and whether a superlative ("the tallest", "the most") wants one row or every row tied for first. Keep the alternative when the wording leaves it open ("list the names of cards with ..." can mean distinct names or one per card; "how many types" can count distinct types or records) and drop it when the wording fixes it ("how many cards" counts records; "list the different ..." is distinct; "the one with the highest" wants one row). For these, the only allowed rewrite_type is the one the heuristic alternative names.

Priors are rough probabilities that the question meant that reading; they need not sum to one. Keep at most 2 alternatives per dependency. Omit any alternative you would give a prior below 0.2: listing it is the same as calling it implausible, and a hedged alternative ('the question says X, but...') is not an alternative. Never propose that a code means the opposite of its conventional reading ('+' meaning negative, 'F' meaning not-full) unless the evidence rows support it. Do propose: a code set when a question's term plausibly spans several codes; a sibling column when the question's wording fits it as well as the agent's; a dropped filter when the agent turned a descriptive phrase into a predicate. Drop every dependency whose agent reading is clearly the only plausible one by returning it with an empty alternatives list. Look hardest at undocumented codes (single letters, abbreviations, flags) whose meaning the question's wording does not pin down, and at qualifiers in the question that the agent may have mapped to the wrong code or column."""

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
                                "rewrite_type": {"type": "string", "enum": ["replace_literal", "literal_set", "replace_column", "include_nulls", "empty_as_null", "coalesce_zero", "count_star", "drop_predicate", "like_contains", "exact_match", "add_distinct", "drop_distinct", "count_distinct", "count_all", "include_ties"]},
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


MIN_PRIOR = 0.25  # BIRD run 4: 0.2 gives 24% flip at 46% judged noise, 0.25 gives 16% at 25%


def merge_llm_readings(deps: list[Dependency], answer: dict, pq: ParsedQuery, db: Database, min_prior: float = MIN_PRIOR) -> list[Dependency]:
    """Apply the LLM's verdicts: keep the dependencies it kept, adopt its alternatives and priors.

    Alternatives below min_prior are dropped: the model listed them but does not believe them."""
    by_id = {d.id: d for d in deps}
    out: list[Dependency] = []
    for item in answer.get("dependencies", []):
        d = by_id.get(item.get("id"))
        if d is None:
            continue
        alts = []
        for a in item.get("alternatives", [])[:MAX_ALTERNATIVES_PER_DEP]:
            prior = float(a.get("prior", 0.3))
            if prior < min_prior:
                continue
            rw = _rewrite_from_llm(a, d, db)
            if rw is None:
                continue
            alts.append(Reading("", a.get("description", ""), rw, prior, "llm"))
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
        if float(item.get("prior", 0.3)) < min_prior:
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
    if t in ("drop_predicate", "like_contains", "exact_match"):
        if d.kind != "value_literal":
            return None
        return Rewrite(t)
    if t in DUPLICATE_REWRITES:
        if d.kind != "duplicates" or len(d.readings) < 2:
            return None
        return Rewrite(d.readings[1].rewrite.type)  # the shape fixes which toggle applies
    return None


DUPLICATE_REWRITES = ("add_distinct", "drop_distinct", "count_distinct", "count_all", "include_ties")


def _find_column_site(table: str, column: str, pq: ParsedQuery, db: Database):
    for site in column_sites(pq, db):
        if site.table.lower() == table.lower() and site.column.lower() == column.lower():
            return site.table, site.column, site.node_ids[0]
    return None
