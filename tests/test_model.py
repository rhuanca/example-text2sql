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


class TestCompositeRelationship(unittest.TestCase):
    def _model(self, also):
        data = {
            "name": "x",
            "tables": [
                {"name": "f", "table": "f"},
                {"name": "d", "table": "d", "primary_key": "id"},
            ],
            "facts": [
                {"table": "f", "name": "aid", "column": "aid"},
                {"table": "f", "name": "ent", "column": "entity"},
                {"table": "d", "name": "dent", "column": "entity"},
            ],
            "relationships": [{"from": "f.aid", "to": "d.id", "also": also}],
        }
        return build_model(data)

    def test_parses_two_pairs(self):
        m = self._model(["f.entity = d.entity"])
        rel = m.relationship_between("f", "d")
        self.assertEqual(rel.column_pairs, [("aid", "id"), ("entity", "entity")])
        # convenience accessors still point at the first pair
        self.assertEqual((rel.from_column, rel.to_column), ("aid", "id"))
        # both join columns are known physical columns on each side
        self.assertIn("entity", m.physical_columns("f"))
        self.assertIn("entity", m.physical_columns("d"))

    def test_also_side_order_is_normalized(self):
        # writing the dim side first still yields (from_column, to_column)
        m = self._model(["d.entity = f.entity"])
        self.assertEqual(m.relationship_between("f", "d").column_pairs[1], ("entity", "entity"))

    def test_also_referencing_wrong_table_rejected(self):
        with self.assertRaises(ValueError):
            self._model(["f.entity = other.entity"])


class TestTableKeys(unittest.TestCase):
    def test_keys_declare_join_columns_without_a_fact(self):
        # a foreign/join key lives on the table via `keys`, not in `facts`
        data = {
            "name": "x",
            "tables": [
                {"name": "f", "table": "f", "keys": ["fk"]},
                {"name": "d", "table": "d", "primary_key": "id"},
            ],
            "relationships": [{"from": "f.fk", "to": "d.id"}],
        }
        m = build_model(data)  # validates: fk is a declared column via keys
        self.assertIn("fk", m.physical_columns("f"))

    def test_composite_primary_key_columns_are_physical(self):
        data = {
            "name": "x",
            "tables": [{"name": "a", "table": "a", "primary_key": ["id", "entity"]}],
        }
        m = build_model(data)
        self.assertEqual(set(m.physical_columns("a")), {"id", "entity"})

    def test_facts_hold_only_measures_in_qbo_model(self):
        from pathlib import Path

        from text2sql.semantic.model import load_model

        qbo = Path(__file__).resolve().parents[1] / "models" / "qbo.yml"
        m = load_model(qbo)
        self.assertEqual(
            {f.name for f in m.facts},
            {"amount", "invoice_line_amount", "invoice_qty"},
        )


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
