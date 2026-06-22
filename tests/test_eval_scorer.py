import unittest

from text2sql.engine.ir import SemanticQuery
from text2sql.eval.scorer import _component, result_sets_match, score_ir


def ir(d):
    return SemanticQuery.from_dict(d)


class TestScoreIR(unittest.TestCase):
    def test_exact_match_is_order_insensitive_for_sets(self):
        expected = ir({"metrics": ["a", "b"], "dimensions": ["x", "y"]})
        predicted = ir({"metrics": ["b", "a"], "dimensions": ["y", "x"]})
        s = score_ir(expected, predicted)
        self.assertTrue(s.exact)
        self.assertEqual(s.metrics.precision, 1.0)
        self.assertEqual(s.metrics.recall, 1.0)
        self.assertEqual(s.dimensions.f1, 1.0)

    def test_missing_metric_lowers_recall(self):
        expected = ir({"metrics": ["a", "b"], "dimensions": []})
        predicted = ir({"metrics": ["a"], "dimensions": []})
        s = score_ir(expected, predicted)
        self.assertEqual(s.metrics.precision, 1.0)
        self.assertEqual(s.metrics.recall, 0.5)
        self.assertFalse(s.exact)

    def test_extra_metric_lowers_precision(self):
        expected = ir({"metrics": ["a"], "dimensions": []})
        predicted = ir({"metrics": ["a", "b"], "dimensions": []})
        s = score_ir(expected, predicted)
        self.assertEqual(s.metrics.precision, 0.5)
        self.assertEqual(s.metrics.recall, 1.0)
        self.assertFalse(s.exact)

    def test_empty_predicted_nonempty_expected_is_zero_precision(self):
        s = _component(set(), {"a"})
        self.assertEqual(s.precision, 0.0)
        self.assertEqual(s.recall, 0.0)
        self.assertEqual(s.f1, 0.0)

    def test_nonempty_predicted_empty_expected_is_perfect_recall(self):
        s = _component({"a"}, set())
        self.assertEqual(s.precision, 0.0)
        self.assertEqual(s.recall, 1.0)

    def test_empty_components_score_perfect(self):
        expected = ir({"metrics": ["a"], "dimensions": []})
        predicted = ir({"metrics": ["a"], "dimensions": []})
        s = score_ir(expected, predicted)
        self.assertEqual(s.dimensions.precision, 1.0)
        self.assertEqual(s.dimensions.recall, 1.0)

    def test_filters_compared_as_set(self):
        expected = ir(
            {
                "metrics": ["a"],
                "dimensions": [],
                "filters": [
                    {"field": "market", "op": "=", "value": "Houston"},
                    {"field": "year", "op": ">", "value": 2024},
                ],
            }
        )
        predicted = ir(
            {
                "metrics": ["a"],
                "dimensions": [],
                "filters": [
                    {"field": "year", "op": ">", "value": 2024},
                    {"field": "market", "op": "=", "value": "Houston"},
                ],
            }
        )
        s = score_ir(expected, predicted)
        self.assertTrue(s.exact)
        self.assertEqual(s.filters.f1, 1.0)

    def test_list_valued_filter_is_hashable(self):
        expected = ir(
            {
                "metrics": ["a"],
                "dimensions": [],
                "filters": [{"field": "market", "op": "in", "value": ["A", "B"]}],
            }
        )
        predicted = ir(
            {
                "metrics": ["a"],
                "dimensions": [],
                "filters": [{"field": "market", "op": "in", "value": ["A", "B"]}],
            }
        )
        self.assertTrue(score_ir(expected, predicted).exact)

    def test_order_by_difference_breaks_exact_but_not_components(self):
        expected = ir(
            {"metrics": ["a"], "dimensions": ["w"], "order_by": [{"field": "w", "dir": "asc"}]}
        )
        predicted = ir(
            {"metrics": ["a"], "dimensions": ["w"], "order_by": [{"field": "w", "dir": "desc"}]}
        )
        s = score_ir(expected, predicted)
        self.assertFalse(s.exact)
        self.assertEqual(s.metrics.f1, 1.0)
        self.assertEqual(s.dimensions.f1, 1.0)

    def test_limit_and_time_affect_exact(self):
        expected = ir({"metrics": ["a"], "dimensions": [], "limit": 5})
        predicted = ir({"metrics": ["a"], "dimensions": [], "limit": 10})
        self.assertFalse(score_ir(expected, predicted).exact)

        expected = ir(
            {"metrics": ["a"], "dimensions": [], "time": {"field": "date", "last_n_days": 30}}
        )
        predicted = ir(
            {"metrics": ["a"], "dimensions": [], "time": {"field": "date", "last_n_days": 7}}
        )
        self.assertFalse(score_ir(expected, predicted).exact)


class TestResultSetsMatch(unittest.TestCase):
    def test_unordered_multiset_match(self):
        cols = ["m", "n"]
        a = [("x", 1), ("y", 2)]
        b = [("y", 2), ("x", 1)]
        self.assertTrue(result_sets_match(cols, a, cols, b, ordered=False))

    def test_ordered_match_requires_same_order(self):
        cols = ["m", "n"]
        a = [("x", 1), ("y", 2)]
        b = [("y", 2), ("x", 1)]
        self.assertFalse(result_sets_match(cols, a, cols, b, ordered=True))
        self.assertTrue(result_sets_match(cols, a, cols, a, ordered=True))

    def test_numeric_normalization(self):
        cols = ["m"]
        self.assertTrue(result_sets_match(cols, [(5,)], cols, [(5.0,)], ordered=True))

    def test_column_order_insensitive(self):
        a = [("Houston", 100)]
        b = [(100, "Houston")]
        self.assertTrue(
            result_sets_match(["market", "sales"], a, ["sales", "market"], b, ordered=True)
        )

    def test_column_set_mismatch_fails(self):
        self.assertFalse(
            result_sets_match(["m"], [(1,)], ["n"], [(1,)], ordered=False)
        )

    def test_row_count_mismatch_fails(self):
        cols = ["m"]
        self.assertFalse(
            result_sets_match(cols, [(1,), (2,)], cols, [(1,)], ordered=False)
        )

    def test_value_mismatch_fails(self):
        cols = ["m"]
        self.assertFalse(result_sets_match(cols, [(1,)], cols, [(2,)], ordered=False))

    def test_duplicate_column_names_fail(self):
        # Ambiguous column mapping must be rejected, not silently compared.
        self.assertFalse(
            result_sets_match(["s", "s"], [(1, 2)], ["s", "s"], [(1, 2)], ordered=False)
        )


if __name__ == "__main__":
    unittest.main()
