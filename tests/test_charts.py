import unittest

from text2sql.chat.charts import choose_chart, is_time_like
from text2sql.engine.ir import SemanticQuery


def ir(metrics, dimensions):
    return SemanticQuery(metrics=metrics, dimensions=dimensions)


class TestChooseChart(unittest.TestCase):
    def test_scalar_is_number(self):
        spec = choose_chart(ir(["total_net_sales"], []), ["total_net_sales"], [(123.0,)])
        self.assertEqual(spec.kind, "number")
        self.assertEqual(spec.y, ["total_net_sales"])

    def test_is_time_like_uses_declared_type(self):
        types = {"txn_month": "month", "weekday_label": "text", "market": "text"}
        self.assertTrue(is_time_like("txn_month", types))       # declared month
        self.assertFalse(is_time_like("weekday_label", types))  # text -> not time
        self.assertFalse(is_time_like("market", types))

    def test_is_time_like_name_fallback_without_types(self):
        self.assertTrue(is_time_like("iso_week"))   # fallback name heuristic
        self.assertFalse(is_time_like("market"))

    def test_two_categorical_dims_is_heatmap(self):
        cols = ["market", "product", "total_net_sales"]
        rows = [("N", "Cap", 3.0), ("S", "Cap", 5.0),
                ("N", "Latte", 2.0), ("S", "Latte", 9.0)]
        spec = choose_chart(ir(["total_net_sales"], ["market", "product"]), cols, rows,
                            types={"market": "text", "product": "text"})
        self.assertEqual(spec.kind, "heatmap")
        # fewer-distinct dim on x (market=2), higher on y via series (product=2 here)
        self.assertIn(spec.x, ("market", "product"))
        self.assertEqual(spec.y, ["total_net_sales"])
        self.assertIsNotNone(spec.series)
        self.assertNotEqual(spec.x, spec.series)

    def test_two_dims_over_cardinality_cap_falls_back_to_table(self):
        # both dims vary, but one has 30 distinct (> cap) -> heatmap unreadable -> table
        cols = ["market", "product", "total_net_sales"]
        rows = [(m, f"p{i}", float(i)) for m in ("N", "S") for i in range(30)]
        spec = choose_chart(ir(["total_net_sales"], ["market", "product"]), cols, rows,
                            types={"market": "text", "product": "text"})
        self.assertEqual(spec.kind, "table")

    def test_time_dimension_by_type_is_line(self):
        cols = ["txn_month", "total_amount"]
        rows = [(1, 100.0), (2, 120.0), (3, 90.0)]
        spec = choose_chart(ir(["total_amount"], ["txn_month"]), cols, rows,
                            types={"txn_month": "month"})
        self.assertEqual(spec.kind, "line")

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

    def test_single_metric_category_is_horizontal(self):
        # top-N shape: one measure over a category -> horizontal bar (rendered
        # sorted by the measure).
        cols = ["product_name", "units_sold"]
        rows = [("Cappuccino", 4983), ("Vanilla Latte", 4056), ("Americano", 3491)]
        spec = choose_chart(ir(["units_sold"], ["product_name"]), cols, rows)
        self.assertEqual(spec.kind, "bar")
        self.assertEqual(spec.orientation, "horizontal")
        self.assertEqual(spec.x, "product_name")
        self.assertEqual(spec.y, ["units_sold"])

    def test_multi_metric_category_is_horizontal_small_multiples(self):
        # units + dollars over a category: horizontal, and y carries BOTH
        # measures so the app renders one bar chart per measure (never one axis).
        cols = ["product_name", "total_net_sales", "units_sold"]
        rows = [("Cappuccino", 100.0, 40), ("Americano", 80.0, 30)]
        spec = choose_chart(
            ir(["total_net_sales", "units_sold"], ["product_name"]), cols, rows
        )
        self.assertEqual(spec.kind, "bar")
        self.assertEqual(spec.orientation, "horizontal")
        self.assertEqual(spec.x, "product_name")
        self.assertEqual(spec.y, ["total_net_sales", "units_sold"])

    def test_same_unit_measures_group(self):
        # both USD -> may share one axis as a grouped bar
        cols = ["store_id", "total_net_sales", "total_budget"]
        rows = [("ST001", 100.0, 110.0), ("ST002", 80.0, 85.0)]
        units = {"total_net_sales": "usd", "total_budget": "usd"}
        spec = choose_chart(
            ir(["total_net_sales", "total_budget"], ["store_id"]), cols, rows, units=units
        )
        self.assertEqual(spec.kind, "bar")
        self.assertEqual(spec.orientation, "grouped")

    def test_different_unit_measures_are_small_multiples(self):
        # dollars vs a count -> never one axis; stays horizontal small multiples
        cols = ["product_name", "total_net_sales", "units_sold"]
        rows = [("Cappuccino", 100.0, 40), ("Americano", 80.0, 30)]
        units = {"total_net_sales": "usd", "units_sold": "count"}
        spec = choose_chart(
            ir(["total_net_sales", "units_sold"], ["product_name"]), cols, rows, units=units
        )
        self.assertEqual(spec.orientation, "horizontal")

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

    def test_single_measure_split_over_time_is_stacked_bar(self):
        # one measure (net sales) split by a categorical (market) over time: the
        # parts sum to each week's total -> stacked bar, not overlaid lines.
        cols = ["iso_week", "market", "total_net_sales"]
        rows = [
            (10, "Houston", 50.0), (10, "Dallas", 30.0),
            (11, "Houston", 60.0), (11, "Dallas", 40.0),
        ]
        spec = choose_chart(
            ir(["total_net_sales"], ["iso_week", "market"]), cols, rows
        )
        self.assertEqual(spec.kind, "bar")
        self.assertEqual(spec.orientation, "stacked")
        self.assertEqual(spec.x, "iso_week")
        self.assertEqual(spec.series, "market")

    def test_prefixed_time_dim_is_line(self):
        # QBO model names its time dims txn_month / txn_year (not bare "month")
        cols = ["txn_month", "total_amount"]
        rows = [(1, 12800.0), (2, 14080.0), (3, 15360.0)]
        spec = choose_chart(ir(["total_amount"], ["txn_month"]), cols, rows)
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "txn_month")

    def test_contrasting_split_over_time_is_a_line_not_stacked(self):
        # total_amount split by classification over month. Revenue/Expense are
        # contrasting (additive:false in the model), so their sum is meaningless ->
        # compare with a multi-series line instead of a stacked bar.
        cols = ["txn_year", "txn_month", "classification", "total_amount"]
        rows = [
            (2026, 1, "Expense", 10240.0), (2026, 1, "Revenue", 12800.0),
            (2026, 2, "Expense", 11264.0), (2026, 2, "Revenue", 14080.0),
            (2026, 3, "Expense", 12288.0), (2026, 3, "Revenue", 15360.0),
        ]
        args = (ir(["total_amount"], ["txn_year", "txn_month", "classification"]),
                cols, rows)
        # contrasting -> line
        spec = choose_chart(*args, additive={"classification": False})
        self.assertEqual(spec.kind, "line")
        self.assertEqual(spec.x, "txn_month")
        self.assertEqual(spec.series, "classification")
        # an additive split (default) still stacks
        self.assertEqual(choose_chart(*args).orientation, "stacked")

    def test_two_low_cardinality_dims_is_heatmap(self):
        # two small categorical dims x one measure now render as a heatmap matrix
        cols = ["market", "category_name", "total_net_sales"]
        rows = [
            ("Houston", "Donuts", 50.0), ("Houston", "Beverages", 20.0),
            ("Dallas", "Donuts", 30.0), ("Dallas", "Beverages", 10.0),
        ]
        spec = choose_chart(
            ir(["total_net_sales"], ["market", "category_name"]), cols, rows
        )
        self.assertEqual(spec.kind, "heatmap")

    def test_no_metric_is_table(self):
        spec = choose_chart(ir([], ["market"]), ["market"], [("Houston",)])
        self.assertEqual(spec.kind, "table")

    def test_empty_rows_is_table(self):
        spec = choose_chart(ir(["total_net_sales"], ["market"]), ["market", "total_net_sales"], [])
        self.assertEqual(spec.kind, "table")


if __name__ == "__main__":
    unittest.main()
