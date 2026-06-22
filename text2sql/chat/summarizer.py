"""Turn a query result into a short, plain-language answer.

The summary is additive: if it fails (no key, network error) the app still shows
the chart and table. Two implementations behind one protocol: a deterministic
MockSummarizer for tests, and AnthropicSummarizer for the real app.
"""

from __future__ import annotations

from typing import Protocol

_SYSTEM = (
    "You summarize SQL query results for a business user in 1-2 short sentences. "
    "Cite the key numbers from the data. Do not invent values or add caveats. "
    "If the result is empty, say no matching data was found."
)


class Summarizer(Protocol):
    def summarize(self, question: str, columns: list[str], rows: list) -> str:
        ...


class MockSummarizer:
    def summarize(self, question, columns, rows) -> str:
        return f"{len(rows)} row(s) for: {question}"


def render_table(columns: list[str], rows: list, max_rows: int = 50) -> str:
    """Compact text rendering of a result set for the prompt."""
    out = [" | ".join(columns)]
    for r in rows[:max_rows]:
        out.append(" | ".join("" if v is None else str(v) for v in r))
    if len(rows) > max_rows:
        out.append(f"... ({len(rows) - max_rows} more rows)")
    return "\n".join(out)


def build_summary_prompt(question: str, columns: list[str], rows: list, max_rows: int = 50) -> str:
    return (
        f"Question: {question}\n\n"
        f"Results ({len(rows)} rows):\n{render_table(columns, rows, max_rows)}"
    )


class AnthropicSummarizer:
    def __init__(self, client=None, model: str | None = None, max_tokens: int = 300, max_rows: int = 50):
        from .. import config

        self.model = model or config.get_model()
        self.max_tokens = max_tokens
        self.max_rows = max_rows
        if client is None:
            key = config.get_api_key()
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            import anthropic

            client = anthropic.Anthropic(api_key=key)
        self.client = client

    def summarize(self, question, columns, rows) -> str:
        prompt = build_summary_prompt(question, columns, rows, self.max_rows)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()
