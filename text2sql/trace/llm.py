"""Anthropic client construction + forced-tool response parsing — shared by the
planner, rewriter, and summarizer so the LangSmith wrap and the tool-extraction
loop live in one place."""

from __future__ import annotations

import os


def build_anthropic_client(key: str):
    """An Anthropic client, wrapped with LangSmith when LANGSMITH_TRACING is set
    (a no-op otherwise). Callers check for a missing key and raise first."""
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    if os.environ.get("LANGSMITH_TRACING", "").lower() in ("1", "true"):
        from langsmith.wrappers import wrap_anthropic

        client = wrap_anthropic(client)
    return client


def tool_input(resp, tool_name: str, key: str):
    """The `key` field of a forced `tool_name` tool-use block in an Anthropic
    response, or None if absent — callers supply their own fallback."""
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input.get(key)
    return None
