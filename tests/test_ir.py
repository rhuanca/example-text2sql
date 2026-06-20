import unittest

from text2sql.engine.ir import FILTER_OPS, IR_JSON_SCHEMA, SemanticQuery


class TestIR(unittest.TestCase):
    def test_round_trip(self):
        d = {
            "metrics": ["total_net_sales"],
            "dimensions": ["product_name", "iso_week"],
            "filters": [{"field": "product_name", "op": "=", "value": "Dozen Glazed"}],
            "time": {"field": "date", "last_n_days": 42},
            "order_by": [{"field": "iso_week", "dir": "asc"}],
            "limit": 100,
        }
        ir = SemanticQuery.from_dict(d)
        self.assertEqual(ir.metrics, ["total_net_sales"])
        self.assertEqual(ir.time.last_n_days, 42)
        self.assertEqual(ir.to_dict(), d)

    def test_rejects_bad_op(self):
        with self.assertRaises(ValueError):
            SemanticQuery.from_dict(
                {"metrics": ["m"], "filters": [{"field": "f", "op": "~=", "value": 1}]}
            )

    def test_schema_lists_ops(self):
        ops = IR_JSON_SCHEMA["properties"]["filters"]["items"]["properties"]["op"]["enum"]
        self.assertEqual(set(ops), set(FILTER_OPS))


if __name__ == "__main__":
    unittest.main()
