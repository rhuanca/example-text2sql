"""The eval scorecard + committed-history helpers (quality-over-time tracking)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from text2sql.eval.history import Scorecard, append_scorecard, load_history


def _report(acc=0.8, npass=8, total=10, fm=0.9, fd=0.85, ff=1.0):
    # Scorecard.from_report reads only these aggregate properties off a Report.
    return SimpleNamespace(exec_accuracy=acc, n_passed=npass, total=total,
                           mean_metric_f1=fm, mean_dimension_f1=fd, mean_filter_f1=ff)


class TestScorecard(unittest.TestCase):
    def test_from_report_captures_headline_numbers(self):
        sc = Scorecard.from_report(_report(), "product_sales",
                                   timestamp="2026-07-19T00:00:00+00:00", git_sha="abc123")
        self.assertEqual(sc.model, "product_sales")
        self.assertEqual(sc.git_sha, "abc123")
        self.assertEqual(sc.exec_accuracy, 0.8)
        self.assertEqual((sc.n_pass, sc.n_total), (8, 10))
        self.assertEqual((sc.f1_metrics, sc.f1_dimensions, sc.f1_filters), (0.9, 0.85, 1.0))
        self.assertIn("model", sc.to_dict())

    def test_from_report_defaults_timestamp_and_sha(self):
        sc = Scorecard.from_report(_report(), "m")
        self.assertTrue(sc.timestamp)      # an ISO timestamp was filled in
        self.assertTrue(sc.git_sha)        # a sha (or "unknown") was filled in


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "sub" / "history.jsonl"  # parent auto-created

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_history_is_empty(self):
        self.assertEqual(load_history(self.path), [])

    def test_append_and_load_round_trip_in_order(self):
        append_scorecard(self.path, Scorecard.from_report(_report(acc=0.7), "sales",
                                                          timestamp="t1", git_sha="s1"))
        append_scorecard(self.path, Scorecard.from_report(_report(acc=0.9), "sales",
                                                          timestamp="t2", git_sha="s2"))
        hist = load_history(self.path)
        self.assertEqual([h["exec_accuracy"] for h in hist], [0.7, 0.9])  # append order
        self.assertEqual([h["git_sha"] for h in hist], ["s1", "s2"])

    def test_load_skips_blank_and_bad_lines(self):
        append_scorecard(self.path, Scorecard.from_report(_report(), "m",
                                                          timestamp="t", git_sha="s"))
        with self.path.open("a") as f:
            f.write("\n")           # blank line
            f.write("not json\n")   # a corrupt line
        self.assertEqual(len(load_history(self.path)), 1)


class TestEvalSummary(unittest.TestCase):
    def test_summary_reports_latest_and_delta_per_model(self):
        from text2sql.chat import app
        history = [
            {"model": "sales", "exec_accuracy": 0.70, "n_pass": 7, "n_total": 10,
             "f1_metrics": 0.8, "f1_dimensions": 0.8, "f1_filters": 0.8},
            {"model": "qbo", "exec_accuracy": 0.90, "n_pass": 9, "n_total": 10,
             "f1_metrics": 1.0, "f1_dimensions": 1.0, "f1_filters": 1.0},
            {"model": "sales", "exec_accuracy": 0.85, "n_pass": 17, "n_total": 20,
             "f1_metrics": 0.9, "f1_dimensions": 0.9, "f1_filters": 0.9},
        ]
        summary = {s["model"]: s for s in app.eval_summary(history)}
        self.assertEqual(summary["sales"]["accuracy"], 0.85)          # latest
        self.assertAlmostEqual(summary["sales"]["delta"], 0.15)       # 0.85 - 0.70 (up)
        self.assertEqual(summary["sales"]["runs"], 2)
        self.assertEqual(summary["sales"]["n_pass"], 17)              # from the latest run
        self.assertEqual(summary["qbo"]["delta"], 0.0)               # single run -> flat


if __name__ == "__main__":
    unittest.main()
