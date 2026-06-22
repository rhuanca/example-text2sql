import tempfile
import unittest
from pathlib import Path

from text2sql.db.seed import build_database
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.ir import SemanticQuery
from text2sql.engine.planner import MockPlanner
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
            "metrics": ["total_net_sales", "total_budget"],
            "dimensions": ["store_id"],
        },
    ),
]


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
        engine = self.make_engine(MockPlanner(RULES))
        result = engine.ask("How is Cappuccino performing week over week?")
        self.assertEqual(len(result.rows), 2)
        self.assertIn("total_net_sales", result.columns)
        self.assertTrue(result.sql.lower().startswith("select"))

    def test_budget_question(self):
        engine = self.make_engine(MockPlanner(RULES))
        result = engine.ask("Show budget vs actual by store")
        self.assertIn("total_budget", result.columns)
        self.assertEqual(len(result.rows), 2)


class FlakyPlanner:
    """Returns a bad IR first, then a good one once an error is reported."""

    def __init__(self, bad, good):
        self.bad, self.good = bad, good

    def plan(self, question, model, error=None):
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
