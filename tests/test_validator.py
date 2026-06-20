import unittest

from text2sql.engine.ir import SemanticQuery
from text2sql.engine.validator import (
    ValidationError,
    validate_ir,
    validate_sql,
)
from tests.util import load_sales_model


class TestValidateSQL(unittest.TestCase):
    def test_allows_select(self):
        validate_sql("SELECT 1")
        validate_sql("WITH x AS (SELECT 1) SELECT * FROM x")

    def test_rejects_non_select(self):
        with self.assertRaises(ValidationError):
            validate_sql("UPDATE sales SET x = 1")

    def test_rejects_forbidden_keyword(self):
        with self.assertRaises(ValidationError):
            validate_sql("SELECT 1; DROP TABLE sales")

    def test_rejects_multiple_statements(self):
        with self.assertRaises(ValidationError):
            validate_sql("SELECT 1; SELECT 2")


class TestValidateIR(unittest.TestCase):
    def setUp(self):
        self.model = load_sales_model()

    def test_unknown_metric(self):
        ir = SemanticQuery.from_dict({"metrics": ["nope"], "dimensions": []})
        with self.assertRaises(ValidationError):
            validate_ir(ir, self.model)

    def test_unknown_dimension(self):
        ir = SemanticQuery.from_dict(
            {"metrics": ["total_net_sales"], "dimensions": ["nope"]}
        )
        with self.assertRaises(ValidationError):
            validate_ir(ir, self.model)

    def test_order_by_not_selected(self):
        ir = SemanticQuery.from_dict(
            {
                "metrics": ["total_net_sales"],
                "dimensions": ["product_name"],
                "order_by": [{"field": "iso_week", "dir": "asc"}],
            }
        )
        with self.assertRaises(ValidationError):
            validate_ir(ir, self.model)

    def test_valid_ir_passes(self):
        ir = SemanticQuery.from_dict(
            {"metrics": ["total_net_sales"], "dimensions": ["product_name"]}
        )
        validate_ir(ir, self.model)  # should not raise


if __name__ == "__main__":
    unittest.main()
