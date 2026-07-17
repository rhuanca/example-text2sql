import unittest

from text2sql.engine.ir import SemanticQuery


class TestIR(unittest.TestCase):
    def test_round_trip(self):
        d = {
            "metrics": ["total_net_sales"],
            "dimensions": ["product_name", "iso_week"],
            "filters": [{"field": "product_name", "op": "=", "value": "Dozen Glazed"}],
            "time": {"field": "date", "last": 42, "unit": "day", "anchor": "data"},
            "order_by": [{"field": "iso_week", "dir": "asc"}],
            "limit": 100,
        }
        ir = SemanticQuery.from_dict(d)
        self.assertEqual(ir.metrics, ["total_net_sales"])
        self.assertEqual(ir.time.last, 42)
        self.assertEqual(ir.to_dict(), d)

    def test_legacy_last_n_days_maps_to_window(self):
        ir = SemanticQuery.from_dict(
            {"metrics": ["m"], "time": {"field": "date", "last_n_days": 30}}
        )
        self.assertEqual((ir.time.last, ir.time.unit, ir.time.anchor), (30, "day", "data"))

    def test_rejects_bad_op(self):
        with self.assertRaises(ValueError):
            SemanticQuery.from_dict(
                {"metrics": ["m"], "filters": [{"field": "f", "op": "~=", "value": 1}]}
            )

if __name__ == "__main__":
    unittest.main()
