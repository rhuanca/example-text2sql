"""Token-usage capture seam.

Each real LLM call records an `LlmCall` into a contextvar-scoped collector; the
app opens one collector per turn (`with collect() as calls:`) and hands the
accrued calls to the trace store. No framework, no I/O, no signature changes to
the planner/summarizer protocols — stub implementations in tests simply never
call `record()`, so the engine's tests and the compiler/validator stay pure.

A contextvar (not instance state) is used because the Streamlit engine is shared
across sessions via `st.cache_resource`; each turn runs in its own ScriptRunner
thread, so a per-turn `collect()` is correctly isolated across concurrent users.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass

# None when no turn is collecting; otherwise the active turn's list of LlmCalls.
_current: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "llm_calls", default=None
)


@dataclass
class LlmCall:
    role: str  # rewrite | plan | summary
    model: str
    input_tokens: int
    output_tokens: int
    ms: float = 0.0


@contextlib.contextmanager
def collect():
    """Collect the LlmCalls recorded within the block into a fresh list, yielded
    to the caller. Nested/parallel blocks each get their own isolated list."""
    calls: list[LlmCall] = []
    token = _current.set(calls)
    try:
        yield calls
    finally:
        _current.reset(token)


def record(call: LlmCall) -> None:
    """Append a call to the active collector, or no-op when none is active."""
    calls = _current.get()
    if calls is not None:
        calls.append(call)


def record_usage(role: str, model: str, resp, ms: float = 0.0) -> None:
    """Pull input/output tokens off an Anthropic response's `.usage` and record
    them. Tolerant: a response without usage (some mock clients) is ignored."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    record(
        LlmCall(
            role=role,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            ms=ms,
        )
    )


def totals(calls: list[LlmCall]) -> tuple[int, int]:
    """(input_tokens, output_tokens) summed over a list of LlmCalls."""
    return (
        sum(c.input_tokens for c in calls),
        sum(c.output_tokens for c in calls),
    )
