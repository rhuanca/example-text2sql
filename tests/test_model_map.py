import unittest
from pathlib import Path

from text2sql.chat.model_map import classify_tables, model_to_dot, table_fields
from text2sql.semantic.model import load_model
from tests.util import load_sales_model

REPO_ROOT = Path(__file__).resolve().parents[1]
QBO_MODEL = REPO_ROOT / "models" / "qbo.yml"


class TestClassify(unittest.TestCase):
    def test_qbo_fact_vs_dim(self):
        kinds = classify_tables(load_model(QBO_MODEL))
        self.assertEqual(kinds["txn"], "fact")
        self.assertEqual(kinds["invoices"], "fact")
        self.assertEqual(kinds["accounts"], "dim")
        self.assertEqual(kinds["acct_hier"], "dim")
        self.assertEqual(kinds["class_hier"], "dim")

    def test_sales_fact_vs_dim(self):
        kinds = classify_tables(load_sales_model())
        self.assertEqual(kinds["fact_sales"], "fact")
        self.assertEqual(kinds["fact_budget"], "fact")
        self.assertEqual(kinds["dim_store"], "dim")


class TestTableFields(unittest.TestCase):
    def test_txn_metrics(self):
        model = load_model(QBO_MODEL)
        names = {m.name for m in table_fields(model, "txn")["metrics"]}
        self.assertEqual(names, {"total_amount", "net_income", "transaction_count"})


class TestDot(unittest.TestCase):
    def setUp(self):
        self.dot = model_to_dot(load_model(QBO_MODEL))

    def test_is_a_digraph(self):
        self.assertTrue(self.dot.strip().startswith("digraph"))
        self.assertTrue(self.dot.strip().endswith("}"))

    def test_has_every_physical_table(self):
        for phys in [
            "qbo_txn_consolidated", "qbo_invoices", "qbo_accounts",
            "hierarchy_by_account", "hierarchy_by_class",
        ]:
            self.assertIn(phys, self.dot)

    def test_has_join_key_edges(self):
        self.assertIn("AccountID = Id", self.dot)
        self.assertIn("Account_Number = AcctNum", self.dot)
        self.assertIn("Class = Class", self.dot)
        self.assertIn("txn -> accounts", self.dot)

    def test_labels_facts_and_dims_and_metrics(self):
        self.assertIn("(FACT)", self.dot)
        self.assertIn("(DIM)", self.dot)
        self.assertIn("total_amount", self.dot)


if __name__ == "__main__":
    unittest.main()
