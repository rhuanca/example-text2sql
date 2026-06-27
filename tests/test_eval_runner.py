import tempfile
import unittest
from pathlib import Path

from text2sql.db.seed import build_database
from text2sql.engine.dialects.sqlite import SqliteDialect
from text2sql.engine.executor import SqliteExecutor
from text2sql.engine.ir import SemanticQuery
from text2sql.eval.dataset import load_cases
from text2sql.eval.report import format_report
from text2sql.eval.runner import run_suite
from tests.util import load_sales_model

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "cases.yml"


def perfect_rules(cases):
    """Rules that reproduce each case's expected IR. Longest questions first so
    no question is shadowed by a shorter substring."""
    rules = [(c.question, c.expected.to_dict()) for c in cases]
    rules.sort(key=lambda kv: len(kv[0]), reverse=True)
    return rules


class _PlannerFromRules:
    """Test stub planner: matches the full question exactly (no substring
    ambiguity), falling back to a configured override per question."""

    def __init__(self, by_question, override=None):
        self.by_question = by_question
        self.override = override or {}

    def plan(self, question, model, error=None):
        if question in self.override:
            return SemanticQuery.from_dict(self.override[question])
        return SemanticQuery.from_dict(self.by_question[question])


class EvalRunnerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = build_database(Path(cls.tmp.name) / "demo.db")
        cls.model = load_sales_model()
        cls.dialect = SqliteDialect()
        cls.cases = load_cases(CASES_PATH)
        cls.by_q = {c.question: c.expected.to_dict() for c in cls.cases}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def executor(self):
        return SqliteExecutor(self.db)

    def test_perfect_planner_scores_100(self):
        planner = _PlannerFromRules(self.by_q)
        report = run_suite(
            self.cases, planner, self.model, self.dialect, self.executor()
        )
        self.assertEqual(report.total, len(self.cases))
        self.assertEqual(report.exec_accuracy, 1.0)
        self.assertEqual(report.exact_ir_rate, 1.0)
        self.assertEqual(report.mean_metric_f1, 1.0)
        for r in report.results:
            self.assertIsNone(r.error)
            self.assertTrue(r.passed)

    def test_report_formats(self):
        planner = _PlannerFromRules(self.by_q)
        report = run_suite(
            self.cases, planner, self.model, self.dialect, self.executor()
        )
        text = format_report(report)
        self.assertIn("execution accuracy", text)
        self.assertIn("sales-by-market", text)

    def test_wrong_but_valid_ir_fails_case(self):
        # For "sales by market" return a different (valid) grouping -> rows differ.
        override = {
            "What were total net sales by market?": {
                "metrics": ["total_net_sales"],
                "dimensions": ["region"],
            }
        }
        planner = _PlannerFromRules(self.by_q, override)
        report = run_suite(
            self.cases, planner, self.model, self.dialect, self.executor()
        )
        bad = next(r for r in report.results if r.id == "sales-by-market")
        self.assertFalse(bad.passed)
        self.assertFalse(bad.exact_ir)
        self.assertIsNone(bad.error)  # it ran fine, the answer was just wrong
        self.assertLess(report.exec_accuracy, 1.0)

    def test_also_accept_alternative_reading_passes(self):
        # Return the grouped reading for houston-sales; it must still pass via
        # the case's also_accept alternative (same total, extra constant column).
        override = {
            "Total net sales in the Houston market": {
                "metrics": ["total_net_sales"],
                "dimensions": ["market"],
                "filters": [{"field": "market", "op": "=", "value": "Houston"}],
            }
        }
        planner = _PlannerFromRules(self.by_q, override)
        report = run_suite(
            self.cases, planner, self.model, self.dialect, self.executor()
        )
        houston = next(r for r in report.results if r.id == "houston-sales")
        self.assertTrue(houston.passed)
        self.assertFalse(houston.exact_ir)  # differs from the primary expected
        self.assertEqual(report.exec_accuracy, 1.0)

    def test_invalid_ir_is_captured_not_raised(self):
        override = {
            "What were total net sales by market?": {
                "metrics": ["does_not_exist"],
                "dimensions": [],
            }
        }
        planner = _PlannerFromRules(self.by_q, override)
        report = run_suite(
            self.cases, planner, self.model, self.dialect, self.executor()
        )
        bad = next(r for r in report.results if r.id == "sales-by-market")
        self.assertFalse(bad.passed)
        self.assertIsNotNone(bad.error)


if __name__ == "__main__":
    unittest.main()
