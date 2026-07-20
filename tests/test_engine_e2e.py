import tempfile
import unittest
from pathlib import Path

from text2sql.db.seed import DIM_STORE, WEEKS, YEARS, build_database
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.ir import SemanticQuery
from tests.util import load_sales_model

RULES = [
    (
        "cappuccino",
        {
            "metrics": ["total_net_sales", "units_sold"],
            "dimensions": ["product_name", "iso_year", "iso_week"],
            "filters": [{"field": "product_name", "op": "=", "value": "Cappuccino"}],
            "order_by": [{"field": "iso_week", "dir": "asc"}],
        },
    ),
    (
        "budget",
        {
            "metrics": ["total_net_sales", "sales_goal"],
            "dimensions": ["store_id"],
        },
    ),
    (
        "past month",
        {
            "metrics": ["total_net_sales"],
            "dimensions": ["market"],
            "time": {"field": "date", "last": 1, "unit": "month"},
        },
    ),
]


class RulePlanner:
    """Test stub: returns a canned IR by case-insensitive substring match."""

    def __init__(self, rules):
        self.rules = rules

    def plan(self, question, model, error=None, history=None):
        q = question.lower()
        for key, ir in self.rules:
            if key.lower() in q:
                return SemanticQuery.from_dict(ir)
        raise AssertionError(f"no rule matched question: {question!r}")


class EngineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo.db")
        cls.model = load_sales_model()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def make_engine(self, planner):
        return Engine(self.model, planner, SqliteDialect(), SqliteExecutor(self.db))


class TestEngineE2E(EngineCase):
    def test_dozen_glazed_question(self):
        engine = self.make_engine(RulePlanner(RULES))
        result = engine.ask("How is Cappuccino performing week over week?")
        # Cappuccino sells every week of every year -> one row per (year, week).
        self.assertEqual(len(result.rows), len(YEARS) * len(WEEKS))
        self.assertIn("total_net_sales", result.columns)
        self.assertTrue(result.sql.lower().startswith("select"))

    def test_budget_question(self):
        engine = self.make_engine(RulePlanner(RULES))
        result = engine.ask("Show budget vs actual by store")
        self.assertIn("sales_goal", result.columns)
        self.assertEqual(len(result.rows), len(DIM_STORE))

    def test_resolves_relative_window_period(self):
        # a plan with a last_period window gets its concrete bucket(s) resolved onto
        # the Result (single-unit window -> start == end), so the UI can show the month.
        engine = self.make_engine(RulePlanner(RULES))
        result = engine.ask("sales for the past month")
        self.assertIsNotNone(result.period_start)
        self.assertEqual(result.period_start, result.period_end)   # one month bucket
        self.assertRegex(result.period_start, r"^\d{4}-\d{2}-01$")  # date_trunc('month')

    def test_no_window_leaves_period_none(self):
        engine = self.make_engine(RulePlanner(RULES))
        result = engine.ask("Show budget vs actual by store")
        self.assertIsNone(result.period_start)
        self.assertIsNone(result.period_end)

    def test_ask_accepts_thread_id(self):
        # thread_id is LangSmith metadata; passing it must not affect the answer,
        # and is a harmless no-op with tracing off.
        engine = self.make_engine(RulePlanner(RULES))
        result = engine.ask("Show budget vs actual by store", thread_id="t-123")
        self.assertEqual(len(result.rows), len(DIM_STORE))


class FlakyPlanner:
    """Returns a bad IR first, then a good one once an error is reported."""

    def __init__(self, bad, good):
        self.bad, self.good = bad, good

    def plan(self, question, model, error=None, history=None):
        ir = self.good if error else self.bad
        return SemanticQuery.from_dict(ir)


class TestRepairLoop(EngineCase):
    def test_repairs_after_bad_first_plan(self):
        planner = FlakyPlanner(
            bad={"metrics": ["does_not_exist"], "dimensions": []},
            good={"metrics": ["total_net_sales"], "dimensions": ["iso_week"]},
        )
        engine = self.make_engine(planner)
        result = engine.ask("anything")
        self.assertIn("total_net_sales", result.columns)
        self.assertEqual(result.ir.metrics, ["total_net_sales"])


if __name__ == "__main__":
    unittest.main()
