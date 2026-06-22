import tempfile
import unittest
from pathlib import Path

from text2sql.engine.ir import SemanticQuery
from text2sql.eval.dataset import DatasetError, load_cases


class TestLoadCases(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, text: str) -> str:
        path = Path(self._tmp.name) / "cases.yml"
        path.write_text(text)
        return str(path)

    def test_loads_typed_cases(self):
        path = self._write(
            """
cases:
  - id: by-market
    question: "sales by market"
    tags: [join]
    expected:
      metrics: [total_net_sales]
      dimensions: [market]
"""
        )
        cases = load_cases(path)
        self.assertEqual(len(cases), 1)
        c = cases[0]
        self.assertEqual(c.id, "by-market")
        self.assertEqual(c.question, "sales by market")
        self.assertEqual(c.tags, ["join"])
        self.assertIsInstance(c.expected, SemanticQuery)
        self.assertEqual(c.expected.metrics, ["total_net_sales"])
        self.assertEqual(c.expected.dimensions, ["market"])

    def test_empty_dataset_raises(self):
        path = self._write("cases: []\n")
        with self.assertRaises(DatasetError):
            load_cases(path)

    def test_missing_id_raises(self):
        path = self._write(
            """
cases:
  - question: "no id here"
    expected: {metrics: [x], dimensions: []}
"""
        )
        with self.assertRaises(DatasetError):
            load_cases(path)

    def test_duplicate_id_raises(self):
        path = self._write(
            """
cases:
  - id: dup
    question: "a"
    expected: {metrics: [x], dimensions: []}
  - id: dup
    question: "b"
    expected: {metrics: [y], dimensions: []}
"""
        )
        with self.assertRaises(DatasetError):
            load_cases(path)

    def test_bad_expected_ir_raises(self):
        path = self._write(
            """
cases:
  - id: bad-filter
    question: "broken"
    expected:
      metrics: [x]
      dimensions: []
      filters: [{field: market, op: "??", value: 1}]
"""
        )
        with self.assertRaises(DatasetError):
            load_cases(path)

    def test_loads_real_dataset(self):
        cases_path = Path(__file__).resolve().parents[1] / "eval" / "cases.yml"
        if not cases_path.exists():
            self.skipTest("eval/cases.yml not present yet")
        cases = load_cases(cases_path)
        self.assertGreaterEqual(len(cases), 12)
        ids = [c.id for c in cases]
        self.assertEqual(len(ids), len(set(ids)), "case ids must be unique")


if __name__ == "__main__":
    unittest.main()
