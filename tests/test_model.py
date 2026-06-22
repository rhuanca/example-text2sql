import unittest

from text2sql.semantic.model import build_model
from tests.util import load_sales_model


class TestModelLoad(unittest.TestCase):
    def test_loads_sales_model(self):
        m = load_sales_model()
        self.assertEqual(m.name, "product_sales")
        self.assertEqual(m.metric("total_net_sales").table, "fact_sales")
        self.assertEqual(m.dimension("market").table, "dim_store")
        self.assertIn("revenue", m.metric("total_net_sales").synonyms)

    def test_relationship_between(self):
        m = load_sales_model()
        rel = m.relationship_between("fact_sales", "dim_store")
        self.assertEqual(rel.from_column, "store_id")
        self.assertEqual(rel.to_column, "store_id")

    def test_physical_columns_includes_join_key(self):
        m = load_sales_model()
        self.assertIn("store_id", m.physical_columns("fact_budget"))


class TestModelValidation(unittest.TestCase):
    def test_unknown_metric_table_rejected(self):
        data = {
            "name": "x",
            "tables": [{"name": "sales", "table": "sales"}],
            "metrics": [{"name": "rev", "table": "nope", "sql": "SUM(x)"}],
        }
        with self.assertRaises(ValueError):
            build_model(data)

    def test_dangling_relationship_column_rejected(self):
        data = {
            "name": "x",
            "tables": [
                {"name": "a", "table": "a"},
                {"name": "b", "table": "b", "primary_key": "id"},
            ],
            # a has no column "aid" declared anywhere
            "relationships": [{"from": "a.aid", "to": "b.id"}],
        }
        with self.assertRaises(ValueError):
            build_model(data)

    def test_duplicate_dimension_name_rejected(self):
        data = {
            "name": "x",
            "tables": [{"name": "a", "table": "a"}],
            "dimensions": [
                {"table": "a", "name": "d", "column": "c1"},
                {"table": "a", "name": "d", "column": "c2"},
            ],
        }
        with self.assertRaises(ValueError):
            build_model(data)


if __name__ == "__main__":
    unittest.main()
