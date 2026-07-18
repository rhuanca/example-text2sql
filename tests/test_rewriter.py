"""Conversational query rewrite: a follow-up is decontextualized into a standalone
question so a prior scope (e.g. entity = Contoso SAS) carries forward. Covers the
pure prompt builder, the MockRewriter, the Anthropic path via a fake client, and a
live rewrite (skipped without a key)."""

import unittest
from types import SimpleNamespace

from text2sql.config import get_api_key
from text2sql.engine.rewriter import (
    AnthropicRewriter, MockRewriter, build_rewrite_prompt,
)

HISTORY = [{
    "question": "show me a summary of financial transactions of Contoso SAS",
    "ir": {"metrics": ["total_amount"], "dimensions": ["classification"],
           "filters": [{"field": "entity", "op": "=", "value": "Contoso SAS"}]},
}]


class TestRewritePrompt(unittest.TestCase):
    def test_prompt_surfaces_prior_scope_and_latest_question(self):
        p = build_rewrite_prompt("revenue of the past 6 days", HISTORY)
        self.assertIn("summary of financial transactions of Contoso SAS", p)
        self.assertIn("entity =", p)            # prior filter shown as scope
        self.assertIn("'Contoso SAS'", p)
        self.assertIn("Latest question: revenue of the past 6 days", p)

    def test_mock_rewriter_is_identity(self):
        self.assertEqual(MockRewriter().rewrite("anything", HISTORY), "anything")


class _FakeBlock:
    type = "tool_use"
    name = "emit_question"

    def __init__(self, tool_input):
        self.input = tool_input


class _FakeAnthropic:
    """Records messages.create calls; returns a fixed emit_question tool call."""

    def __init__(self, question):
        self.calls = []
        self._question = question
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[_FakeBlock({"question": self._question})])


class TestAnthropicRewriter(unittest.TestCase):
    def test_returns_tool_question_and_sends_history(self):
        client = _FakeAnthropic("revenue of the past 6 days for Contoso SAS")
        rw = AnthropicRewriter(client=client, model="test-model")
        out = rw.rewrite("revenue of the past 6 days", HISTORY)
        self.assertEqual(out, "revenue of the past 6 days for Contoso SAS")
        content = client.calls[0]["messages"][0]["content"]
        self.assertIn("Contoso SAS", content)  # the scope reached the model
        self.assertEqual(client.calls[0]["tool_choice"]["name"], "emit_question")

    def test_no_history_skips_the_call(self):
        client = _FakeAnthropic("unused")
        rw = AnthropicRewriter(client=client, model="test-model")
        self.assertEqual(rw.rewrite("what data is available?", []), "what data is available?")
        self.assertEqual(client.calls, [])  # no LLM call when there's nothing to carry


@unittest.skipUnless(get_api_key(), "ANTHROPIC_API_KEY not set; skipping live rewrite")
class TestAnthropicRewriterLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rw = AnthropicRewriter()

    def test_carries_prior_entity(self):
        out = self.rw.rewrite("show me the revenue of the past 6 days", HISTORY).lower()
        self.assertIn("contoso", out)  # the entity scope is carried forward

    def test_broaden_drops_the_scope(self):
        out = self.rw.rewrite(
            "show me the revenue of the past 6 days for all entities", HISTORY
        ).lower()
        self.assertNotIn("contoso", out)  # broadening clears the carried scope


if __name__ == "__main__":
    unittest.main()
