import unittest
from types import SimpleNamespace

from tests.util import load_sales_model
from text2sql.chat import app, plots
from text2sql.chat.charts import ChartSpec
from text2sql.engine.compare import Comparison


class TestAppHelpers(unittest.TestCase):
    def test_import_does_not_launch(self):
        # importing the module must not require a running Streamlit server
        self.assertTrue(hasattr(app, "main"))

    def test_to_frame(self):
        df = app.to_frame(["a", "b"], [(1, 2), (3, 4)])
        self.assertEqual(list(df.columns), ["a", "b"])
        self.assertEqual(len(df), 2)

    def test_chart_frame_line(self):
        spec = ChartSpec("line", x="iso_week", y=["total_net_sales"])
        df = app.chart_frame(spec, ["iso_week", "total_net_sales"], [(10, 37.47), (11, 49.96)])
        self.assertEqual(df.index.name, "iso_week")
        self.assertEqual(list(df.columns), ["total_net_sales"])

    def test_chart_frame_multiseries_pivot(self):
        spec = ChartSpec("line", x="iso_week", y=["total_net_sales"], series="market")
        rows = [
            (10, "Houston", 50.0), (10, "Dallas", 30.0),
            (11, "Houston", 60.0), (11, "Dallas", 40.0),
        ]
        df = app.chart_frame(spec, ["iso_week", "market", "total_net_sales"], rows)
        self.assertEqual(df.index.name, "iso_week")
        self.assertEqual(set(df.columns), {"Houston", "Dallas"})

    def test_horizontal_bar_sorts_by_metric_descending(self):
        df = app.to_frame(
            ["product_name", "units_sold"],
            [("Cappuccino", 4983), ("Americano", 3491), ("Vanilla Latte", 4056)],
        )
        chart = app.horizontal_bar(df, "product_name", "units_sold")
        spec = chart.to_dict()  # Vega-Lite spec
        # a bar layer plus a text (value-label) layer
        marks = {layer["mark"] if isinstance(layer["mark"], str)
                 else layer["mark"]["type"] for layer in spec["layer"]}
        self.assertEqual(marks, {"bar", "text"})
        # the category axis is sorted by the metric, descending
        y_enc = spec["layer"][0]["encoding"]["y"]
        self.assertEqual(y_enc["field"], "product_name")
        self.assertEqual(y_enc["sort"], "-x")
        self.assertEqual(spec["layer"][0]["encoding"]["x"]["field"], "units_sold")
        # dataviz palette: bars use categorical slot 1, labels are comma-formatted
        self.assertEqual(spec["layer"][0]["mark"]["color"], plots.SERIES_1)
        self.assertEqual(spec["layer"][1]["encoding"]["text"]["format"], ",")

    def test_horizontal_bar_shared_order(self):
        # an explicit sort list forces a fixed category order (used to align
        # small multiples), instead of sorting each chart by its own measure.
        df = app.to_frame(
            ["product_name", "total_net_sales"],
            [("Cappuccino", 21177.75), ("Americano", 12218.5)],
        )
        order = ["Americano", "Cappuccino"]
        chart = app.horizontal_bar(df, "product_name", "total_net_sales", sort=order)
        y_enc = chart.to_dict()["layer"][0]["encoding"]["y"]
        self.assertEqual(y_enc["sort"], order)

    def test_fmt_number(self):
        self.assertEqual(app._fmt_number(4983), "4,983")
        self.assertEqual(app._fmt_number(159033.65), "159,033.65")
        self.assertEqual(app._fmt_number(2000.0), "2,000")  # whole float -> no decimals
        self.assertEqual(app._fmt_number("N/A"), "N/A")      # non-number passes through

    def test_percent_measure(self):
        self.assertTrue(app._percent_measure("pct_change", {}))
        self.assertFalse(app._percent_measure("total_net_sales", {}))
        self.assertTrue(app._percent_measure("margin", {"margin": "percent"}))

    def test_line_panel_percent_axis_and_zero_line(self):
        df = app.to_frame(["iso_week", "pct_change"], [(47, 2.22), (48, 0.0)])
        spec = app.line_panel(df, "iso_week", "pct_change", percent=True).to_dict()
        self.assertEqual(len(spec["layer"]), 2)  # zero rule + line
        line = next(
            l for l in spec["layer"]
            if (l["mark"]["type"] if isinstance(l["mark"], dict) else l["mark"]) == "line"
        )
        self.assertIn("%", line["encoding"]["y"]["axis"]["labelExpr"])
        self.assertTrue(line["encoding"]["y"]["scale"]["zero"])

    def test_line_panel_plain_no_percent(self):
        df = app.to_frame(["iso_week", "total_net_sales"], [(47, 195.5)])
        spec = app.line_panel(df, "iso_week", "total_net_sales", percent=False).to_dict()
        self.assertNotIn("layer", spec)  # single line, no zero rule
        self.assertNotIn("labelExpr", spec["encoding"]["y"].get("axis") or {})

    def test_fmt_number_by_unit(self):
        self.assertEqual(app._fmt_number(21177.75, "usd"), "$21,177.75")
        self.assertEqual(app._fmt_number(4983, "count"), "4,983")
        self.assertEqual(app._fmt_number(0.123, "percent"), "12.3%")

    def test_horizontal_bar_formats_by_unit(self):
        df = app.to_frame(["product_name", "total_net_sales"], [("Cappuccino", 21177.75)])
        spec = app.horizontal_bar(df, "product_name", "total_net_sales", fmt="$,.2f").to_dict()
        self.assertEqual(spec["layer"][1]["encoding"]["text"]["format"], "$,.2f")

    def test_grouped_bar_colors_by_measure(self):
        df = app.to_frame(
            ["store_id", "total_net_sales", "total_budget"],
            [("ST001", 100.0, 110.0), ("ST002", 80.0, 85.0)],
        )
        spec = app.grouped_bar(
            df, "store_id", ["total_net_sales", "total_budget"], fmt="$,.2f"
        ).to_dict()
        enc = spec["encoding"]
        self.assertEqual(enc["color"]["field"], "measure")
        self.assertEqual(enc["yOffset"]["field"], "measure")  # side-by-side, not stacked
        self.assertEqual(enc["x"]["axis"]["format"], "$,.2f")
        self.assertEqual(enc["color"]["scale"]["range"], [plots.SERIES_1, plots.SERIES_2])

    def test_line_chart_single_uses_palette_blue_and_formats(self):
        df = app.to_frame(["iso_week", "total_net_sales"], [(10, 100.0), (11, 120.0)])
        spec = app.line_chart(df, "iso_week", "total_net_sales", fmt="$,.2f").to_dict()
        self.assertEqual(spec["mark"]["type"], "line")
        self.assertEqual(spec["mark"]["color"], plots.SERIES_1)  # single line = slot 1
        self.assertEqual(spec["encoding"]["y"]["axis"]["format"], "$,.2f")
        self.assertNotIn("color", spec["encoding"])  # no series -> no legend

    def test_line_chart_multiseries_colors_by_category(self):
        df = app.to_frame(
            ["iso_week", "market", "total_net_sales"],
            [(10, "Houston", 50.0), (10, "Dallas", 30.0)],
        )
        spec = app.line_chart(df, "iso_week", "total_net_sales", color="market").to_dict()
        self.assertEqual(spec["encoding"]["color"]["field"], "market")
        self.assertEqual(spec["encoding"]["color"]["scale"]["range"], plots.PALETTE)

    def test_stacked_bar_stacks_and_colors_by_series(self):
        df = app.to_frame(
            ["txn_month", "account", "total_amount"],
            [(7, "Product Sales", 100.0), (7, "Service Revenue", 60.0),
             (8, "Product Sales", 90.0), (8, "Service Revenue", 50.0)],
        )
        spec = app.stacked_bar(
            df, "txn_month", "account", "total_amount", fmt="$,.2f"
        ).to_dict()
        enc = spec["encoding"]
        self.assertEqual(enc["y"]["stack"], "zero")       # stacked, not overlaid
        self.assertEqual(enc["color"]["field"], "account")  # colored by the split
        self.assertEqual(enc["x"]["field"], "txn_month")
        self.assertEqual(enc["y"]["axis"]["format"], "$,.2f")

    def test_metric_units_loaded_from_yaml(self):
        units = {m.name: m.unit for m in load_sales_model().metrics}
        self.assertEqual(units["total_net_sales"], "usd")
        self.assertEqual(units["units_sold"], "count")

    def test_md_safe_escapes_dollars(self):
        self.assertEqual(
            app._md_safe("$21,177.75 in net sales"), "\\$21,177.75 in net sales"
        )

    def test_comparison_long_maps_periods_by_position(self):
        cmp = Comparison.from_dict({
            "metric": "units_sold", "split_by": "product_name",
            "period_field": "iso_week", "periods": [50, 51, 52],
        })
        cols = ["product_name", "units_sold_50", "units_sold_51", "units_sold_52"]
        long = app.comparison_long(cmp, cols, [("Cappuccino", 97, 97, 99)])
        self.assertEqual(len(long), 3)  # one row per (product, period)
        self.assertEqual(sorted(long["period"]), [50, 51, 52])
        got = long[(long["product_name"] == "Cappuccino") & (long["period"] == 52)]
        self.assertEqual(got["value"].iloc[0], 99)  # last column -> last period

    def test_comparison_grouped_bar_colors_by_period(self):
        cmp = Comparison.from_dict({
            "metric": "total_amount", "split_by": "txn_month",
            "period_field": "txn_year", "periods": [2025, 2026],
        })
        cols = ["txn_month", "total_amount_2025", "total_amount_2026"]
        long = app.comparison_long(cmp, cols, [(1, 10.0, 12.0), (2, 9.0, 11.0)])
        spec = app.comparison_grouped_bar(
            long, "txn_month", "txn_year", [2025, 2026], fmt="$,.2f"
        ).to_dict()
        enc = spec["encoding"]
        self.assertEqual(enc["color"]["field"], "period")
        self.assertEqual(enc["yOffset"]["field"], "period")  # side-by-side, not stacked
        self.assertEqual(enc["x"]["axis"]["format"], "$,.2f")
        self.assertEqual(enc["color"]["scale"]["range"], [plots.SERIES_1, plots.SERIES_2])

    def test_display_frame_relabels_comparison_columns(self):
        cmp = Comparison.from_dict({
            "metric": "units_sold", "split_by": "product_name",
            "period_field": "iso_week", "periods": [50, 51],
        })
        result = SimpleNamespace(
            ir=cmp,
            columns=["product_name", "units_sold_50", "units_sold_51"],
            rows=[("Cappuccino", 97, 99)],
        )
        df = app._display_frame(result)
        self.assertIn("units_sold (iso_week=50)", df.columns)
        self.assertIn("units_sold (iso_week=51)", df.columns)

    def test_month_label(self):
        self.assertEqual(plots.month_label("2026-04"), "Apr 2026")   # calendar month
        self.assertEqual(plots.month_label("2026-12"), "Dec 2026")
        self.assertEqual(plots.month_label(4), "Apr")                # month-of-year
        self.assertEqual(plots.month_label("7"), "Jul")
        self.assertEqual(plots.month_label(2026), 2026)              # a year -> unchanged
        self.assertEqual(plots.month_label("Houston"), "Houston")    # passthrough

    def test_month_axis_relabels_and_sorts_chronologically(self):
        df = app.to_frame(["month", "v"], [("2026-04", 1), ("2026-03", 2)])
        out, order = plots._month_axis(df, "month", "month")
        self.assertEqual(list(out["month"]), ["Apr 2026", "Mar 2026"])  # rows in place
        self.assertEqual(order, ["Mar 2026", "Apr 2026"])              # chronological sort
        _, none = plots._month_axis(df, "month", "week")               # non-month
        self.assertIsNone(none)

    def test_display_frame_prettifies_month_columns(self):
        result = SimpleNamespace(
            ir=SimpleNamespace(),  # no period_field
            columns=["month", "total_amount"],
            rows=[("2026-03", 100.0), ("2026-04", 120.0)],
        )
        df = app._display_frame(result, {"month": "month"})
        self.assertEqual(list(df["month"]), ["Mar 2026", "Apr 2026"])
        self.assertEqual(list(df["total_amount"]), [100.0, 120.0])  # non-month untouched

    def test_line_chart_month_axis_is_chronological(self):
        df = app.to_frame(["month", "total_amount"], [("2026-04", 120.0), ("2026-03", 100.0)])
        spec = app.line_chart(df, "month", "total_amount", x_type="month").to_dict()
        self.assertEqual(spec["encoding"]["x"]["sort"], ["Mar 2026", "Apr 2026"])

    def test_safe_summarize_falls_back_on_error(self):
        class Boom:
            def summarize(self, *a):
                raise RuntimeError("no key")

        out = app.safe_summarize(Boom(), "q", ["a"], [(1,)])
        self.assertIn("1 row", out)


if __name__ == "__main__":
    unittest.main()
