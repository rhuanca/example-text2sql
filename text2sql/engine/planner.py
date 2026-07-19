"""Query Planner: natural language -> semantic SQL.

This is the only fuzzy step. The planner writes a single SQL SELECT over a
*virtual* table whose columns are the model's dimensions and metrics; the engine
parses + validates that against the model (`semantic_sql.py`) and lowers it to
physical SQL. The Planner protocol lets the engine depend on an interface rather
than a concrete LLM client; AnthropicPlanner is the real implementation.
"""

from __future__ import annotations

import json
import os
import time
from typing import Protocol

from ..semantic.model import SemanticModel
from ..trace import usage


class PlannerError(Exception):
    pass


class Planner(Protocol):
    def plan(
        self,
        question: str,
        model: SemanticModel,
        error: str | None = None,
        history: list | None = None,
    ) -> str:  # semantic SQL
        ...


_SQL_TOOL_DESC = (
    "Emit ONE SQL SELECT over the semantic table for the user's question. Use only "
    "the listed dimension and metric columns; never join or reference physical "
    "tables or columns."
)

SQL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
}


def _examples(model: SemanticModel) -> list[str]:
    mnames = [m.name for m in model.metrics]
    if not mnames:
        return []
    m0 = mnames[0]
    cat = next(
        (d.name for d in model.dimensions if getattr(d, "type", None) not in ("date", "number")),
        None,
    )
    date_dim = next((d.name for d in model.dimensions if getattr(d, "type", None) == "date"), None)
    week_dim = next((d.name for d in model.dimensions if "week" in d.name.lower()), None)
    out: list[str] = []
    if cat:
        out += [
            f"Q: top 5 {cat} by {m0}",
            f"SQL: SELECT {cat}, {m0} FROM {model.name} GROUP BY {cat} "
            f"ORDER BY {m0} DESC LIMIT 5",
        ]
    if date_dim and week_dim and cat:
        out += [
            f"Q: {m0} week over week over the last 6 weeks",
            f"SQL: SELECT {week_dim}, {m0} FROM {model.name} "
            f"WHERE {date_dim} >= last_period(6, 'week') GROUP BY {week_dim} "
            f"ORDER BY {week_dim}",
        ]
    year_dim = next((d.name for d in model.dimensions if "year" in d.name.lower()), None)
    bucket = week_dim or next(
        (d.name for d in model.dimensions if "month" in d.name.lower()), None
    )
    if year_dim and bucket:
        out += [
            f"Q: compare {m0} by {bucket} for 2025 vs 2026",
            f"SQL: SELECT {bucket}, "
            f"SUM(CASE WHEN {year_dim} = 2025 THEN {m0} END) AS {m0}_2025, "
            f"SUM(CASE WHEN {year_dim} = 2026 THEN {m0} END) AS {m0}_2026 "
            f"FROM {model.name} GROUP BY {bucket} ORDER BY {bucket}",
        ]
    if bucket:
        out += [
            f"Q: {m0} {bucket} over {bucket}, percent change",
            f"SQL: SELECT {bucket}, {m0}, "
            f"100.0 * ({m0} - LAG({m0}) OVER (ORDER BY {bucket})) "
            f"/ LAG({m0}) OVER (ORDER BY {bucket}) AS pct_change "
            f"FROM {model.name} GROUP BY {bucket} ORDER BY {bucket}",
        ]
    return out


def build_system_prompt(model: SemanticModel) -> str:
    lines = [
        "You translate natural-language questions into SQL over a semantic layer.",
        f"Write ONE SQL SELECT against a single virtual table named `{model.name}`.",
        "Its only columns are the dimensions and metrics listed below. Metrics are "
        "already-aggregated measures — select them by name; do NOT wrap them in "
        "SUM/COUNT/etc. Never join, never reference physical tables or columns, and "
        "never use SELECT *.",
    ]
    about = [t for t in model.tables if t.description or t.grain]
    if about:
        lines += ["", "ABOUT THE DATA (background only — you still write ONE SELECT "
                  f"over `{model.name}`; the engine resolves any joins for you):"]
        for t in about:
            desc = " ".join(t.description.split()) if t.description else ""
            grain = f"(grain: {t.grain})" if t.grain else ""
            body = " ".join(p for p in [desc, grain] if p)
            lines.append(f"- {t.name} — {body}")
    lines += ["", "DIMENSIONS (group-by / filter columns):"]
    for d in model.dimensions:
        bits = []
        if d.synonyms:
            bits.append("synonyms: " + ", ".join(d.synonyms))
        if getattr(d, "sample_values", None):
            bits.append("examples: " + ", ".join(str(v) for v in d.sample_values))
        meta = f"  [{'; '.join(bits)}]" if bits else ""
        desc = f" — {d.description}" if getattr(d, "description", "") else ""
        lines.append(f"- {d.name}{desc}{meta}")
    lines += ["", "METRICS (already aggregated — select by name):"]
    for m in model.metrics:
        bits = []
        if m.synonyms:
            bits.append("synonyms: " + ", ".join(m.synonyms))
        if getattr(m, "unit", None):
            bits.append(f"unit: {m.unit}")
        meta = f"  [{'; '.join(bits)}]" if bits else ""
        desc = f" — {m.description}" if getattr(m, "description", "") else ""
        lines.append(f"- {m.name}{desc}{meta}")
    lines += [
        "",
        "RULES:",
        "- SELECT only dimension and metric columns by name; GROUP BY every "
        "dimension you select alongside a metric.",
        "- WHERE compares a dimension to a literal with =, !=, <, <=, >, >=, IN, "
        "NOT IN, or LIKE. Combine conditions with AND.",
        "- Relative time: use last_period(N, 'day'|'week'|'month') on a date "
        "dimension, e.g. `WHERE <date_dim> >= last_period(6, 'week')`. It resolves "
        "to the last N periods present in the DATA — never write today's date, a "
        "literal date range, or guessed week/month numbers.",
        "- Filter aggregated measures with HAVING (e.g. `HAVING <metric> > 1000`).",
        "- Rank with `ORDER BY <col> [DESC]` and `LIMIT N`.",
        "- For 'week over week' or a trend over many periods, SELECT the time "
        "dimension plus any category so it renders as a line (do NOT pivot).",
        "- To compare a metric across a FEW named periods side by side (e.g. 2025 "
        "vs 2026), write a CASE pivot: one `AGG(CASE WHEN <period_dim> = <value> "
        "THEN <metric> END)` column per period, grouped by the row bucket — it "
        "renders as a grouped bar. Use the metric's own aggregate (SUM for a sum "
        "metric).",
    ]
    # Prefer the model's curated verified queries (question -> semantic SQL);
    # fall back to generated examples for a model that declares none.
    examples = _verified_examples(model) or _examples(model)
    if examples:
        lines += ["", "EXAMPLES:"] + examples
    return "\n".join(lines)


def _verified_examples(model: SemanticModel) -> list[str]:
    out: list[str] = []
    for v in model.verified_queries:
        out.append(f"Q: {v.question}")
        out.append("SQL: " + " ".join(v.sql.split()))  # normalize folded whitespace
    return out


def _history_block(history: list) -> str:
    """Render prior turns as context so follow-ups resolve. Each turn is
    {"question": str, "ir": dict} — the normalized query the turn produced."""
    lines = [
        "CONVERSATION SO FAR (most recent last). Use it to resolve follow-up "
        "questions like 'and for 2026' or 'break that down by class': carry over "
        "the previous columns/filters unless the user changes them.",
    ]
    for turn in history:
        lines.append(f"Q: {turn['question']}")
        lines.append(f"Computed: {json.dumps(turn['ir'])}")
    return "\n".join(lines)


class AnthropicPlanner:
    """LLM planner backed by the Anthropic API. Output is constrained to a forced
    `emit_sql` tool call, so it returns exactly one SQL string that the engine
    then validates against the model."""

    def __init__(self, client=None, model: str | None = None, max_tokens: int = 1024):
        from .. import config

        self.model = model or config.get_model()
        self.max_tokens = max_tokens
        if client is None:
            key = config.get_api_key()
            if not key:
                raise PlannerError("ANTHROPIC_API_KEY is not set")
            import anthropic

            client = anthropic.Anthropic(api_key=key)
            # Optional LangSmith tracing (no-op unless LANGSMITH_TRACING is set).
            if os.environ.get("LANGSMITH_TRACING", "").lower() in ("1", "true"):
                from langsmith.wrappers import wrap_anthropic

                client = wrap_anthropic(client)
        self.client = client

    def plan(self, question, model, error=None, history=None) -> str:
        content = question
        if error:
            content += (
                f"\n\nThe previous SQL failed with: {error}\n"
                "Return corrected SQL that avoids this error."
            )
        if history:
            content = f"{_history_block(history)}\n\nCurrent question: {content}"
        t0 = time.monotonic()
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=build_system_prompt(model),
            tools=[
                {
                    "name": "emit_sql",
                    "description": _SQL_TOOL_DESC,
                    "input_schema": SQL_TOOL_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "emit_sql"},
            messages=[{"role": "user", "content": content}],
        )
        usage.record_usage("plan", self.model, resp, (time.monotonic() - t0) * 1000)
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_sql":
                return block.input["sql"]
        raise PlannerError("planner did not return an emit_sql tool call")
