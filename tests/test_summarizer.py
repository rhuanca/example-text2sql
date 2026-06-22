import os
import unittest

from text2sql.chat.summarizer import (
    AnthropicSummarizer,
    MockSummarizer,
    build_summary_prompt,
    render_table,
)
from text2sql.config import get_api_key


class TestPrompt(unittest.TestCase):
    def test_render_table_caps_rows(self):
        cols = ["a"]
        rows = [(i,) for i in range(60)]
        text = render_table(cols, rows, max_rows=50)
        self.assertIn("... (10 more rows)", text)

    def test_prompt_includes_question_and_data(self):
        prompt = build_summary_prompt(
            "by market?", ["market", "total_net_sales"], [("Houston", 100.0)]
        )
        self.assertIn("by market?", prompt)
        self.assertIn("Houston", prompt)
        self.assertIn("100.0", prompt)


class TestMockSummarizer(unittest.TestCase):
    def test_deterministic(self):
        s = MockSummarizer()
        self.assertEqual(s.summarize("q", ["a"], [(1,), (2,)]), "2 row(s) for: q")


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResp:
    def __init__(self, text):
        self.content = [FakeBlock(text)]


class FakeClient:
    def __init__(self, text):
        self._text = text
        self.last_kwargs = None

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.last_kwargs = kwargs
            return FakeResp(self.outer._text)

    @property
    def messages(self):
        return FakeClient._Messages(self)


class TestAnthropicSummarizerWithFakeClient(unittest.TestCase):
    def test_returns_text_and_sends_prompt(self):
        client = FakeClient("Sales rose to 100 in Houston.")
        s = AnthropicSummarizer(client=client, model="test-model")
        out = s.summarize("by market?", ["market", "total_net_sales"], [("Houston", 100.0)])
        self.assertEqual(out, "Sales rose to 100 in Houston.")
        sent = client.last_kwargs["messages"][0]["content"]
        self.assertIn("Houston", sent)


@unittest.skipUnless(get_api_key(), "ANTHROPIC_API_KEY not set; skipping live test")
class TestAnthropicSummarizerLive(unittest.TestCase):
    def test_live_summary(self):
        s = AnthropicSummarizer()
        out = s.summarize(
            "net sales by market",
            ["market", "total_net_sales"],
            [("Houston", 100.0), ("Dallas", 60.0)],
        )
        self.assertTrue(out.strip())


if __name__ == "__main__":
    unittest.main()
