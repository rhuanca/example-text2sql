import tempfile
import unittest
from pathlib import Path

from text2sql.config import get_api_key
from text2sql.db.seed import build_database
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.planner import AnthropicPlanner, build_system_prompt
from text2sql.engine.validator import validate_ir
from tests.util import load_sales_model


class TestSystemPrompt(unittest.TestCase):
    """Prompt construction needs no API key and is always exercised."""

    def test_prompt_lists_fields(self):
        prompt = build_system_prompt(load_sales_model())
        self.assertIn("total_net_sales", prompt)
        self.assertIn("product_name", prompt)
        self.assertIn("EXAMPLES:", prompt)


@unittest.skipUnless(get_api_key(), "ANTHROPIC_API_KEY not set; skipping live test")
class TestAnthropicPlannerLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_sales_model()
        cls.planner = AnthropicPlanner()

    def test_produces_valid_ir(self):
        ir = self.planner.plan(
            "How is Dozen Glazed performing week over week?", self.model
        )
        validate_ir(ir, self.model)  # must reference only real fields
        self.assertTrue(ir.metrics, "planner returned no metrics")

    def test_end_to_end_against_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = build_database(Path(tmp) / "demo.db")
            engine = Engine(
                self.model, self.planner, SqliteDialect(), SqliteExecutor(db)
            )
            result = engine.ask("What were total net sales by market?")
            self.assertIn("total_net_sales", result.columns)
            self.assertTrue(result.rows)


if __name__ == "__main__":
    unittest.main()
