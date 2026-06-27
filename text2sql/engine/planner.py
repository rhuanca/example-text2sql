"""Query Planner: natural language -> Semantic Query IR.

This is the only fuzzy step. The Planner protocol lets the engine depend on an
interface rather than a concrete LLM client; AnthropicPlanner is the real
implementation.
"""

from __future__ import annotations

import json
from typing import Protocol

from ..semantic.model import SemanticModel
from .ir import IR_JSON_SCHEMA, SemanticQuery


class PlannerError(Exception):
    pass


class Planner(Protocol):
    def plan(
        self, question: str, model: SemanticModel, error: str | None = None
    ) -> SemanticQuery:
        ...


_TOOL_DESC = (
    "Emit the structured query for the user's question. Use ONLY the metrics and "
    "dimensions defined in the semantic model. Never invent field names."
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
    ]
    if model.examples:
        lines.append("")
        lines.append("EXAMPLES:")
        for ex in model.examples:
            lines.append(f"Q: {ex.question}")
            lines.append(f"IR: {json.dumps(ex.ir)}")
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
        self.client = client

    def plan(self, question, model, error=None) -> SemanticQuery:
        content = question
        if error:
            content += (
                f"\n\nA previous attempt failed with: {error}\n"
                "Return a corrected query that avoids this error."
            )
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=build_system_prompt(model),
            tools=[
                {
                    "name": "emit_query",
                    "description": _TOOL_DESC,
                    "input_schema": IR_JSON_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "emit_query"},
            messages=[{"role": "user", "content": content}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_query":
                return SemanticQuery.from_dict(block.input)
        raise PlannerError("planner did not return an emit_query tool call")
