"""Query Planner: natural language -> Semantic Query IR.

This is the only fuzzy step. The Planner protocol lets the engine depend on an
interface rather than a concrete LLM client; AnthropicPlanner is the real
implementation.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from ..semantic.model import SemanticModel
from .compare import COMPARISON_JSON_SCHEMA, Comparison
from .ir import IR_JSON_SCHEMA, SemanticQuery


class PlannerError(Exception):
    pass


class Planner(Protocol):
    def plan(
        self,
        question: str,
        model: SemanticModel,
        error: str | None = None,
        history: list | None = None,
    ) -> SemanticQuery | Comparison:
        ...


_TOOL_DESC = (
    "Emit the structured query for the user's question. Use ONLY the metrics and "
    "dimensions defined in the semantic model. Never invent field names."
)

_COMPARE_DESC = (
    "Emit a period comparison: the same metric across two or more periods, shown "
    "side by side (one column per period). Use ONLY names from the semantic model."
)


def build_system_prompt(model: SemanticModel) -> str:
    lines = [
        "You translate natural-language questions into a structured query (an IR) "
        "for a semantic model. You do NOT write SQL.",
        "Select only from the metrics and dimensions listed below; resolve the "
        "user's wording to these canonical names using the synonyms.",
        "Always answer by calling the emit_query tool.",
        "",
        "METRICS (aggregated measures):",
    ]
    for m in model.metrics:
        syn = f"  [synonyms: {', '.join(m.synonyms)}]" if m.synonyms else ""
        lines.append(f"- {m.name}{syn}")
    lines.append("")
    lines.append("DIMENSIONS (group-by / filter attributes):")
    for d in model.dimensions:
        bits = []
        if d.synonyms:
            bits.append("synonyms: " + ", ".join(d.synonyms))
        if d.sample_values:
            bits.append("examples: " + ", ".join(str(v) for v in d.sample_values))
        meta = f"  [{'; '.join(bits)}]" if bits else ""
        lines.append(f"- {d.name}{meta}")
    lines += [
        "",
        "RULES:",
        "- order_by fields must be among the selected metrics/dimensions.",
        "- For 'last N days' style ranges use the time window (a date dimension + last_n_days).",
        "- Filters compare a dimension to a value with one of: "
        "=, !=, <, <=, >, >=, in, not in, like.",
        "",
        "PERIOD COMPARISON:",
        "- If the user asks to COMPARE a metric across two or more periods side by "
        "side (e.g. 'compare revenue for Jan-Mar between 2025 and 2026', "
        "'this year vs last year by month'), call emit_comparison instead of "
        "emit_query, with:",
        "  - metric: the measure to compare",
        "  - split_by: the dimension for the rows (the finer time bucket, e.g. a "
        "month or week dimension)",
        "  - period_field: the dimension whose values are the periods being "
        "compared (e.g. a year dimension)",
        "  - periods: the list of period values, one column each (e.g. [2025, 2026])",
        "  - filters: any other constraints (e.g. classification = Revenue, or "
        "restricting the split_by to the requested buckets)",
        "- Otherwise, use emit_query for a normal single-answer question.",
    ]
    if model.examples:
        lines.append("")
        lines.append("EXAMPLES:")
        for ex in model.examples:
            lines.append(f"Q: {ex.question}")
            lines.append(f"IR: {json.dumps(ex.ir)}")
    return "\n".join(lines)


def _history_block(history: list) -> str:
    """Render prior turns as a compact block the planner can use to resolve
    follow-ups. Each turn is {"question": str, "ir": dict} (the IR that turn
    produced), most recent last. We carry the IR, not result rows: it is the
    compact record of what was just computed and what a refinement builds on."""
    lines = [
        "CONVERSATION SO FAR (most recent last). Use it to resolve follow-up "
        "questions like 'and for 2026' or 'break that down by class': carry over "
        "the previous metrics/dimensions/filters unless the user changes them.",
    ]
    for turn in history:
        lines.append(f"Q: {turn['question']}")
        lines.append(f"IR: {json.dumps(turn['ir'])}")
    return "\n".join(lines)


class AnthropicPlanner:
    """LLM planner backed by the Anthropic API. The model output is constrained
    to the IR JSON schema via a forced tool call, so it can only return a valid
    Semantic Query shape."""

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
            # Optional LangSmith tracing: wraps the client so every
            # messages.create (system prompt, tools, tool_choice, messages, and
            # the response) is logged. No-op unless LANGSMITH_TRACING is set, so
            # langsmith stays an optional dependency.
            if os.environ.get("LANGSMITH_TRACING", "").lower() in ("1", "true"):
                from langsmith.wrappers import wrap_anthropic

                client = wrap_anthropic(client)
        self.client = client

    def plan(self, question, model, error=None, history=None) -> SemanticQuery:
        content = question
        if error:
            content += (
                f"\n\nA previous attempt failed with: {error}\n"
                "Return a corrected query that avoids this error."
            )
        if history:
            content = f"{_history_block(history)}\n\nCurrent question: {content}"
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=build_system_prompt(model),
            tools=[
                {
                    "name": "emit_query",
                    "description": _TOOL_DESC,
                    "input_schema": IR_JSON_SCHEMA,
                },
                {
                    "name": "emit_comparison",
                    "description": _COMPARE_DESC,
                    "input_schema": COMPARISON_JSON_SCHEMA,
                },
            ],
            # let the model choose emit_query vs emit_comparison, but require one
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": content}],
        )
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if block.name == "emit_query":
                return SemanticQuery.from_dict(block.input)
            if block.name == "emit_comparison":
                return Comparison.from_dict(block.input)
        raise PlannerError("planner did not return a query or comparison tool call")
