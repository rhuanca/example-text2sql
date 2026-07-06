import unittest

from text2sql.chat.charts import choose_chart
from text2sql.engine.ir import SemanticQuery


def ir(metrics, dimensions):
    return SemanticQuery(metrics=metrics, dimensions=dimensions)


class TestChooseChart(unittest.TestCase):
    def test_scalar_is_number(self):
        spec = choose_chart(ir(["total_net_sales"], []), ["total_net_sales"], [(123.0,)])
        self.assertEqual(spec.kind, "number")
        self.assertEqual(spec.y, ["total_net_sales"])

    def test_time_dimension_is_line(self):
        cols = ["iso_week", "total_net_sales"]
        rows = [(10, 37.47), (11, 49.96)]
        spec = choose_chart(ir(["total_net_sales"], ["iso_week"]), cols, rows)
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "iso_week")

    def test_categorical_dimension_is_bar(self):
        cols = ["market", "total_net_sales"]
        rows = [("Houston", 100.0), ("Dallas", 80.0)]
        spec = choose_chart(ir(["total_net_sales"], ["market"]), cols, rows)
        self.assertEqual(spec.kind, "bar")
        self.assertEqual(spec.x, "market")

    def test_constant_dimensions_dropped_wow_is_line(self):
        # the Dozen Glazed WoW shape: product_name + iso_year are constants
        cols = ["product_name", "iso_year", "iso_week", "total_net_sales"]
        rows = [
            ("Dozen Glazed", 2026, 10, 37.47),
            ("Dozen Glazed", 2026, 11, 49.96),
        ]
        spec = choose_chart(
            ir(["total_net_sales"], ["product_name", "iso_year", "iso_week"]),
            cols,
            rows,
        )
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "iso_week")

    def test_two_effective_dims_one_time_is_multiseries_line(self):
        cols = ["iso_week", "market", "total_net_sales"]
        rows = [
            (10, "Houston", 50.0), (10, "Dallas", 30.0),
            (11, "Houston", 60.0), (11, "Dallas", 40.0),
        ]
        spec = choose_chart(
            ir(["total_net_sales"], ["iso_week", "market"]), cols, rows
        )
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "iso_week")
        self.assertEqual(spec.series, "market")

    def test_prefixed_time_dim_is_line(self):
        # QBO model names its time dims txn_month / txn_year (not bare "month")
        cols = ["txn_month", "total_amount"]
        rows = [(1, 12800.0), (2, 14080.0), (3, 15360.0)]
        spec = choose_chart(ir(["total_amount"], ["txn_month"]), cols, rows)
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "txn_month")

    def test_expenses_and_income_by_month_is_multiseries_line(self):
        # the reported case: total_amount by txn_year, txn_month, classification.
        # txn_year is constant (dropped); month is the time axis, classification
        # the series -> a two-line chart, not a table.
        cols = ["txn_year", "txn_month", "classification", "total_amount"]
        rows = [
            (2026, 1, "Expense", 10240.0), (2026, 1, "Revenue", 12800.0),
            (2026, 2, "Expense", 11264.0), (2026, 2, "Revenue", 14080.0),
            (2026, 3, "Expense", 12288.0), (2026, 3, "Revenue", 15360.0),
        ]
        spec = choose_chart(
            ir(["total_amount"], ["txn_year", "txn_month", "classification"]),
            cols,
            rows,
        )
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "txn_month")
        self.assertEqual(spec.series, "classification")

    def test_two_categorical_dims_is_table(self):
        cols = ["market", "category_name", "total_net_sales"]
        rows = [
            ("Houston", "Donuts", 50.0), ("Houston", "Beverages", 20.0),
            ("Dallas", "Donuts", 30.0), ("Dallas", "Beverages", 10.0),
        ]
        spec = choose_chart(
            ir(["total_net_sales"], ["market", "category_name"]), cols, rows
        )
        self.assertEqual(spec.kind, "table")

    def test_no_metric_is_table(self):
        spec = choose_chart(ir([], ["market"]), ["market"], [("Houston",)])
        self.assertEqual(spec.kind, "table")

    def test_empty_rows_is_table(self):
        spec = choose_chart(ir(["total_net_sales"], ["market"]), ["market", "total_net_sales"], [])
        self.assertEqual(spec.kind, "table")


if __name__ == "__main__":
    unittest.main()
