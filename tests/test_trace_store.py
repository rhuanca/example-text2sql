"""The local trace store: idempotent schema, INSERT-OR-IGNORE conversations,
a turn + its llm_calls written together, and best-effort writes that never raise."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.trace.store import TraceStore
from text2sql.trace.usage import LlmCall


class TestTraceStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / "traces.db")
        self.store = TraceStore(self.path)
        self.store.init()

    def tearDown(self):
        self.dir.cleanup()

    def _query(self, sql, params=()):
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_init_is_idempotent(self):
        self.store.init()  # second call must not raise or duplicate
        tables = {r[0] for r in self._query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"conversations", "turns", "llm_calls"}, tables)

    def test_record_turn_dedupes_the_conversation(self):
        # two turns on one thread -> the conversation row is created once (INSERT OR IGNORE)
        self.store.record_turn(thread_id="t1", dataset="sales", question="q1")
        self.store.record_turn(thread_id="t1", dataset="sales", question="q2")
        rows = self._query("SELECT thread_id, dataset FROM conversations")
        self.assertEqual(rows, [("t1", "sales")])
        self.assertEqual(self._query("SELECT COUNT(*) FROM turns")[0][0], 2)

    def test_record_turn_writes_turn_and_calls(self):
        calls = [LlmCall("rewrite", "opus", 20, 4, 11.0),
                 LlmCall("plan", "opus", 200, 50, 90.0),
                 LlmCall("summary", "opus", 120, 30, 40.0)]
        turn_id = self.store.record_turn(
            thread_id="t1", dataset="sales", question="net sales by month",
            rewritten=None, semantic_sql="SELECT month, total_net_sales ...",
            sql="SELECT ...", row_count=12, chart_kind="line", error=None,
            latency_ms=310.5, calls=calls)
        self.assertIsInstance(turn_id, int)
        # parent conversation auto-created
        self.assertEqual(self._query("SELECT COUNT(*) FROM conversations")[0][0], 1)
        turn = self._query(
            "SELECT thread_id, dataset, question, row_count, chart_kind, latency_ms "
            "FROM turns WHERE turn_id=?", (turn_id,))
        self.assertEqual(turn, [("t1", "sales", "net sales by month", 12, "line", 310.5)])
        roles = self._query(
            "SELECT role, input_tokens, output_tokens FROM llm_calls WHERE turn_id=? "
            "ORDER BY id", (turn_id,))
        self.assertEqual(roles, [("rewrite", 20, 4), ("plan", 200, 50), ("summary", 120, 30)])

    def test_record_turn_with_no_calls(self):
        turn_id = self.store.record_turn(thread_id="t1", dataset="sales",
                                         question="q", error="boom", calls=[])
        self.assertIsInstance(turn_id, int)
        self.assertEqual(self._query("SELECT COUNT(*) FROM llm_calls")[0][0], 0)
        self.assertEqual(self._query("SELECT error FROM turns")[0][0], "boom")

    def test_writes_are_best_effort(self):
        bad = TraceStore("/nonexistent-dir/nope/traces.db")
        # neither raises despite an unwritable path
        self.assertIsNone(bad.init())
        self.assertIsNone(bad.record_turn(thread_id="t1", question="q"))


if __name__ == "__main__":
    unittest.main()
