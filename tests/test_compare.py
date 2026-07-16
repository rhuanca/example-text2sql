"""Period comparison: the structured request -> deterministic pivot SQL, its
guardrails, the engine path, and chart selection."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from text2sql.chat.charts import choose_chart
from text2sql.db.seed_qbo import build_database
from text2sql.engine.compare import (
    CompareError,
    Comparison,
    compile_comparison,
    validate_comparison,
)
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.engine import Engine
from text2sql.engine.executor import SqliteExecutor
from text2sql.semantic.model import load_model

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = REPO_ROOT / "models" / "qbo.yml"

REVENUE_JAN_MAR = {
    "metric": "total_amount",
    "split_by": "txn_month",
    "period_field": "txn_year",
    "periods": [2025, 2026],
    "filters": [
        {"field": "classification", "op": "=", "value": "Revenue"},
        {"field": "txn_month", "op": "in", "value": [1, 2, 3]},
    ],
}


class CompareCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo_qbo.db")
        cls.model = load_model(MODEL_PATH)
        cls.dialect = SqliteDialect()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def raw(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class TestCompilePivot(CompareCase):
    def test_pivot_shape_and_values(self):
        cmp = Comparison.from_dict(REVENUE_JAN_MAR)
        sql, params = compile_comparison(cmp, self.model, self.dialect)
        conn = sqlite3.connect(self.db)
        try:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            conn.close()

        # one metric column per period, one row per requested month
        self.assertEqual(cols, ["txn_month", "total_amount_2025", "total_amount_2026"])
        self.assertEqual([r[0] for r in rows], [1, 2, 3])

        # each period column matches the raw per-(month, year) revenue
        for month, rev25, rev26 in rows:
            for year, got in ((2025, rev25), (2026, rev26)):
                expected = self.raw(
                    "SELECT SUM(CAST(t.Amount AS REAL)) FROM qbo_txn_consolidated t "
                    "JOIN qbo_accounts a ON t.AccountID = a.Id AND t.Entity = a.Entity "
                    "WHERE a.Classification = 'Revenue' AND t.Month = ? AND t.Year = ?",
                    (month, year),
                )[0][0]
                self.assertAlmostEqual(got, expected, places=2)

    def test_params_are_bound_not_interpolated(self):
        cmp = Comparison.from_dict(REVENUE_JAN_MAR)
        sql, params = compile_comparison(cmp, self.model, self.dialect)
        # period value compared via a placeholder, not a literal (2025 only
        # appears as the column alias total_amount_2025)
        self.assertIn('base."txn_year" = ?', sql)
        self.assertNotIn("= 2025", sql)
        self.assertNotIn("Revenue", sql)  # filter value is bound too
        self.assertEqual(params[:2], [2025, 2026])  # SELECT placeholders bind first


class TestValidation(CompareCase):
    def test_unknown_metric_rejected(self):
        cmp = Comparison.from_dict({**REVENUE_JAN_MAR, "metric": "nope"})
        with self.assertRaises(CompareError):
            validate_comparison(cmp, self.model)

    def test_unknown_dimension_rejected(self):
        cmp = Comparison.from_dict({**REVENUE_JAN_MAR, "split_by": "nope"})
        with self.assertRaises(CompareError):
            validate_comparison(cmp, self.model)

    def test_needs_two_periods(self):
        cmp = Comparison.from_dict({**REVENUE_JAN_MAR, "periods": [2026]})
        with self.assertRaises(CompareError):
            validate_comparison(cmp, self.model)
        with self.assertRaises(CompareError):
            compile_comparison(cmp, self.model, self.dialect)


class TestChart(unittest.TestCase):
    def test_comparison_is_grouped_bar(self):
        cmp = Comparison.from_dict(REVENUE_JAN_MAR)
        cols = ["txn_month", "total_amount_2025", "total_amount_2026"]
        rows = [(1, 10.0, 12.0), (2, 9.0, 11.0)]
        spec = choose_chart(cmp, cols, rows)
        self.assertEqual(spec.kind, "bar")
        self.assertEqual(spec.x, "txn_month")
        self.assertEqual(spec.y, ["total_amount_2025", "total_amount_2026"])


class _ComparePlanner:
    """Test stub: always returns a fixed Comparison (no LLM)."""

    def __init__(self, cmp):
        self.cmp = cmp

    def plan(self, question, model, error=None, history=None):
        return self.cmp


class TestEngineComparison(CompareCase):
    def test_engine_runs_a_comparison(self):
        engine = Engine(
            self.model,
            _ComparePlanner(Comparison.from_dict(REVENUE_JAN_MAR)),
            self.dialect,
            SqliteExecutor(self.db),
        )
        result = engine.ask("compare revenue for jan-mar between 2025 and 2026")
        self.assertEqual(
            result.columns, ["txn_month", "total_amount_2025", "total_amount_2026"]
        )
        self.assertEqual(len(result.rows), 3)
        # the plan carried on the result is the Comparison, and serializes
        self.assertEqual(result.ir.to_dict()["periods"], [2025, 2026])


if __name__ == "__main__":
    unittest.main()
