"""Short-term conversation memory: prior (question, IR) turns are threaded into
the planner so follow-ups resolve. Covers the app helper that extracts turns,
the engine forwarding the history, and the planner rendering it into the prompt.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from text2sql.chat.app import recent_turns
from text2sql.db.seed_qbo import build_database
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.ir import SemanticQuery
from text2sql.engine.planner import AnthropicPlanner, _history_block
from text2sql.semantic.model import load_model

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = REPO_ROOT / "models" / "qbo.yml"

PRIOR_IR = {"metrics": ["total_amount"], "dimensions": ["txn_month"]}


def _answered(question, ir_dict):
    """A chat-history user+assistant pair as app.py stores them."""
    result = SimpleNamespace(ir=SemanticQuery.from_dict(ir_dict))
    return [
        {"role": "user", "text": question},
        {"role": "assistant", "result": result, "summary": "…"},
    ]


class TestRecentTurns(unittest.TestCase):
    def test_extracts_question_and_ir_pairs(self):
        history = _answered("show revenue by month", PRIOR_IR)
        self.assertEqual(
            recent_turns(history),
            [{"question": "show revenue by month", "ir": PRIOR_IR}],
        )

    def test_skips_error_turns(self):
        history = [
            *_answered("show revenue by month", PRIOR_IR),
            {"role": "user", "text": "oops nonsense"},
            {"role": "assistant", "error": "could not answer"},  # no result -> skip
        ]
        turns = recent_turns(history)
        self.assertEqual([t["question"] for t in turns], ["show revenue by month"])

    def test_caps_at_limit_keeping_most_recent(self):
        history = []
        for i in range(6):
            history += _answered(f"q{i}", PRIOR_IR)
        turns = recent_turns(history, limit=4)
        self.assertEqual([t["question"] for t in turns], ["q2", "q3", "q4", "q5"])


class TestHistoryBlock(unittest.TestCase):
    def test_renders_prior_questions_and_ir(self):
        block = _history_block(
            [{"question": "show revenue by month", "ir": PRIOR_IR}]
        )
        self.assertIn("CONVERSATION SO FAR", block)
        self.assertIn("show revenue by month", block)
        self.assertIn("total_amount", block)  # the prior IR is carried verbatim


class _FakeBlock:
    type = "tool_use"
    name = "emit_query"

    def __init__(self, tool_input):
        self.input = tool_input


class _FakeAnthropic:
    """Records every messages.create call and returns a fixed emit_query."""

    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[_FakeBlock({"metrics": ["total_amount"], "dimensions": ["txn_year"]})]
        )


class TestPlannerPromptCarriesHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(MODEL_PATH)

    def test_history_appears_in_the_user_message(self):
        client = _FakeAnthropic()
        planner = AnthropicPlanner(client=client, model="test-model")
        planner.plan(
            "only for 2026",
            self.model,
            history=[{"question": "show revenue by month", "ir": PRIOR_IR}],
        )
        content = client.calls[0]["messages"][0]["content"]
        self.assertIn("CONVERSATION SO FAR", content)
        self.assertIn("show revenue by month", content)
        self.assertIn("Current question: only for 2026", content)

    def test_no_history_sends_bare_question(self):
        client = _FakeAnthropic()
        planner = AnthropicPlanner(client=client, model="test-model")
        planner.plan("show revenue by month", self.model)
        content = client.calls[0]["messages"][0]["content"]
        self.assertNotIn("CONVERSATION SO FAR", content)
        self.assertEqual(content, "show revenue by month")


class _CapturingPlanner:
    """Records the history it was given, returns a fixed valid IR."""

    def __init__(self, ir):
        self.ir = ir
        self.seen = []

    def plan(self, question, model, error=None, history=None):
        self.seen.append(history)
        return self.ir


class TestEngineForwardsHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo_qbo.db")
        cls.model = load_model(MODEL_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_history_reaches_the_planner(self):
        planner = _CapturingPlanner(
            SemanticQuery.from_dict({"metrics": ["total_amount"], "dimensions": ["txn_year"]})
        )
        engine = Engine(self.model, planner, SqliteDialect(), SqliteExecutor(self.db))
        history = [{"question": "show revenue by month", "ir": PRIOR_IR}]
        result = engine.ask("only for 2026", history=history)

        self.assertEqual(planner.seen[0], history)  # forwarded verbatim
        self.assertIn("total_amount", result.columns)  # and still executes


if __name__ == "__main__":
    unittest.main()
