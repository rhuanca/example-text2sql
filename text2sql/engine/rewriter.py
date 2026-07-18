"""Conversational query rewrite (decontextualization): turn a follow-up question
into a STANDALONE one using the recent turns, so the planner sees a self-contained
request. This is where multi-turn scope carries — a prior `entity = 'Contoso SAS'`
filter rides forward into "revenue of the past 6 days" unless the user broadens or
clears it — instead of relying on the planner to re-emit the filter.

Two implementations behind one protocol: a deterministic MockRewriter (tests / no
key) and AnthropicRewriter (a small forced-tool LLM call, mirroring the planner's
emit_sql). The rewrite is surfaced in the UI ("Interpreted as: …") so a carried
scope is visible and reversible.
"""

from __future__ import annotations

import os
from typing import Protocol

_SYSTEM = (
    "You rewrite a user's latest question into a STANDALONE question for a SQL "
    "analytics assistant, using the conversation so far. Resolve references and "
    "carry forward the active scope — filters like a specific entity/company, time "
    "range, or classification from earlier turns — UNLESS the user:\n"
    "  - names a different value for that field,\n"
    '  - broadens ("all", "overall", "every", "across entities", "combined", "total"), or\n'
    '  - clears/resets ("clear", "reset", "ignore that", "start over", "forget that").\n'
    "If the question is already standalone or clearly starts a new topic, return it "
    "unchanged. Do not answer it or add any explanation — return only the rewritten "
    "question."
)

_QUESTION_TOOL = {
    "name": "emit_question",
    "description": "Emit the single rewritten, standalone question — nothing else.",
    "input_schema": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
}


class Rewriter(Protocol):
    def rewrite(self, question: str, history: list) -> str:  # standalone question
        ...


class MockRewriter:
    """No-op rewriter for tests / when no API key: returns the question unchanged."""

    def rewrite(self, question: str, history: list) -> str:
        return question


def _scope_line(ir: dict) -> str:
    """The prior turn's filters as a readable scope, e.g. `entity = 'Contoso SAS'`."""
    parts = [f"{f['field']} {f['op']} {f['value']!r}" for f in ir.get("filters", [])]
    return ", ".join(parts)


def build_rewrite_prompt(question: str, history: list) -> str:
    """Render the conversation (prior questions + the scope each one computed) plus
    the latest question. Pure/testable — like planner.build_system_prompt."""
    lines = ["CONVERSATION SO FAR (most recent last):"]
    for turn in history:
        lines.append(f"Q: {turn['question']}")
        scope = _scope_line(turn.get("ir") or {})
        if scope:
            lines.append(f"  (scope: {scope})")
    lines += ["", f"Latest question: {question}",
              "Rewrite the latest question so it stands alone."]
    return "\n".join(lines)


class AnthropicRewriter:
    """LLM rewriter backed by the Anthropic API, constrained to a forced
    `emit_question` tool call so it returns exactly one rewritten question."""

    def __init__(self, client=None, model: str | None = None, max_tokens: int = 256):
        from .. import config

        # The rewrite is a simpler task than planning; a faster model (e.g. Haiku)
        # is a fine swap via ANTHROPIC_MODEL if latency matters. Default: same model.
        self.model = model or config.get_model()
        self.max_tokens = max_tokens
        if client is None:
            key = config.get_api_key()
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            import anthropic

            client = anthropic.Anthropic(api_key=key)
            if os.environ.get("LANGSMITH_TRACING", "").lower() in ("1", "true"):
                from langsmith.wrappers import wrap_anthropic

                client = wrap_anthropic(client)
        self.client = client

    def rewrite(self, question: str, history: list) -> str:
        if not history:
            return question  # nothing to carry — skip the call
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM,
            tools=[_QUESTION_TOOL],
            tool_choice={"type": "tool", "name": "emit_question"},
            messages=[{"role": "user", "content": build_rewrite_prompt(question, history)}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_question":
                return block.input["question"].strip() or question
        return question  # defensively fall back to the original
