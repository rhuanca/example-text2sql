import json
import unittest
from types import SimpleNamespace

from tests.util import load_sales_model
from text2sql.chat import app, plots
from text2sql.chat.charts import ChartSpec
from text2sql.engine.compare import Comparison
from text2sql.engine.semantic_sql import QueryShape


def _marks(spec):
    """The set of mark types in a Vega-Lite spec dict, whether it's a single mark
    or a layered chart."""
    layers = spec["layer"] if "layer" in spec else [spec]
    return {(lyr["mark"]["type"] if isinstance(lyr["mark"], dict) else lyr["mark"])
            for lyr in layers}


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

    def test_bucket_long_tail_folds_remainder_into_other(self):
        cols = ["product", "sales"]
        rows = [(f"p{i}", (20 - i)) for i in range(20)]  # 20 categories, descending
        out_cols, out_rows = plots.bucket_long_tail(cols, rows, "product", "sales", top_n=5)
        self.assertEqual(out_cols, cols)
        cats = [r[0] for r in out_rows]
        self.assertEqual(len(out_rows), 6)                 # 5 kept + Other
        self.assertEqual(cats[:5], ["p0", "p1", "p2", "p3", "p4"])
        self.assertEqual(cats[-1], "Other")
        # Other = sum of the folded 15 categories' metric
        folded = sum(20 - i for i in range(5, 20))
        self.assertEqual(out_rows[-1][1], folded)

    def test_bucket_long_tail_noop_when_within_cap(self):
        cols = ["product", "sales"]
        rows = [("a", 3), ("b", 5), ("c", 1)]
        out_cols, out_rows = plots.bucket_long_tail(cols, rows, "product", "sales", top_n=12)
        self.assertEqual(out_rows, rows)                   # unchanged, no Other

    def test_area_chart_single_series_fills_and_titles(self):
        df = app.to_frame(["month", "v"], [("Jan", 3.0), ("Feb", 5.0), ("Mar", 4.0)])
        spec = plots.area_chart(df, "month", "v").to_dict()
        # single-series area is a mark_area (possibly layered with story overlays)
        self.assertIn("area", _marks(spec))

    def test_faceted_line_builds_small_multiples(self):
        df = app.to_frame(["week_start", "account_name", "classification", "total_amount"],
                          [("2026-11-16", "Product Sales", "Revenue", 19000.0),
                           ("2026-11-16", "Payroll Expense", "Expense", 8400.0)])
        spec = plots.faceted_line(df, "week_start", "total_amount", "account_name",
                                  "classification", fmt="$,.0f").to_dict()
        self.assertEqual(spec["facet"]["field"], "classification")   # one panel per facet
        self.assertEqual(spec["spec"]["mark"]["type"], "line")       # each panel is a line
        self.assertEqual(spec["spec"]["encoding"]["color"]["field"], "account_name")

    def test_scatter_chart_two_metrics(self):
        df = app.to_frame(["store", "budget", "actual"], [("A", 10.0, 12.0), ("B", 20.0, 18.0)])
        spec = plots.scatter_chart(df, "budget", "actual", "store").to_dict()
        self.assertEqual(spec["mark"]["type"], "circle")
        self.assertEqual(spec["encoding"]["x"]["field"], "budget")
        self.assertEqual(spec["encoding"]["y"]["field"], "actual")
        self.assertFalse(spec["encoding"]["x"]["scale"]["zero"])  # cloud fills the frame

    def test_heatmap_rect_and_sequential_scale(self):
        df = app.to_frame(["market", "product", "sales"],
                          [("N", "Cap", 3.0), ("S", "Cap", 5.0),
                           ("N", "Latte", 2.0), ("S", "Latte", 9.0)])
        spec = plots.heatmap(df, "market", "product", "sales").to_dict()
        self.assertIn("rect", _marks(spec))
        # sequential single-hue scale (two-stop range: light -> series blue), not rainbow
        rng = spec["layer"][0]["encoding"]["color"]["scale"]["range"]
        self.assertEqual(rng[-1], plots.SERIES_1)
        self.assertEqual(len(rng), 2)
        # column headers stay horizontal so short codes (e.g. state abbreviations)
        # are readable, not rotated vertical
        self.assertEqual(spec["layer"][0]["encoding"]["x"]["axis"]["labelAngle"], 0)

    def test_registered_theme_config_is_applied(self):
        # the central theme merges into every chart's spec (branding in one place)
        df = app.to_frame(["m", "v"], [("A", 3), ("B", 5)])
        cfg = app.horizontal_bar(df, "m", "v").to_dict().get("config", {})
        self.assertIsNone(cfg["view"]["stroke"])              # no chart border
        self.assertEqual(cfg["range"]["category"], plots.PALETTE)  # categorical order
        self.assertEqual(cfg["axis"]["domainColor"], plots.AXIS_LINE)
        self.assertEqual(cfg["title"]["anchor"], "start")

    def test_horizontal_bar_sorts_by_metric_descending(self):
        df = app.to_frame(
            ["product_name", "units_sold"],
            [("Cappuccino", 4983), ("Americano", 3491), ("Vanilla Latte", 4056)],
        )
        chart = app.horizontal_bar(df, "product_name", "units_sold")
        spec = chart.to_dict()  # Vega-Lite spec
        # a bar layer plus a text (value-label) layer
        self.assertEqual(_marks(spec), {"bar", "text"})
        # the category axis is sorted by the metric, descending
        y_enc = spec["layer"][0]["encoding"]["y"]
        self.assertEqual(y_enc["field"], "product_name")
        self.assertEqual(y_enc["sort"], "-x")
        self.assertEqual(spec["layer"][0]["encoding"]["x"]["field"], "units_sold")
        # dataviz palette: bars use categorical slot 1 (now via encoding so a story
        # can grey the non-focus), labels are comma-formatted
        self.assertEqual(spec["layer"][0]["encoding"]["color"]["value"], plots.SERIES_1)
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

    def test_needs_split(self):
        self.assertFalse(plots.needs_split(["a"], {}))                              # single measure
        self.assertFalse(plots.needs_split(["a", "b"], {"a": "usd", "b": "usd"}))   # same known unit
        self.assertTrue(plots.needs_split(["a", "b"], {"a": "usd"}))                # one unit unknown
        self.assertTrue(plots.needs_split(["rev", "pct_change"],                    # different units
                                          {"rev": "usd", "pct_change": "percent"}))

    def test_area_chart_percent_axis(self):
        df = app.to_frame(["month", "pct_change"], [("2026-01-01", -4.5), ("2026-02-01", 2.1)])
        spec = plots.area_chart(df, "month", "pct_change", percent=True).to_dict()
        self.assertIn("+ '%'", json.dumps(spec))       # percent-formatted y-axis
        plain = plots.area_chart(df, "month", "pct_change").to_dict()
        self.assertNotIn("+ '%'", json.dumps(plain))   # non-percent area is unchanged

    def test_render_area_and_line_split_mixed_scale_panels(self):
        """A USD metric next to a scale-less percent change renders as one panel per
        metric (its own axis) for BOTH line and area — the shared split layout."""
        class _CaptureSt:
            def __init__(self): self.charts, self.captions = [], []
            def altair_chart(self, chart, **k): self.charts.append(chart)
            def caption(self, text, *a, **k): self.captions.append(text)
            def columns(self, spec): return [self] * (spec if isinstance(spec, int) else len(spec))
            def metric(self, *a, **k): pass

        result = SimpleNamespace(
            ir=QueryShape(metrics=["total_net_sales", "pct_change"], dimensions=["month"]),
            columns=["month", "total_net_sales", "pct_change"],
            rows=[("2026-01-01", 3000.0, None), ("2026-02-01", 2700.0, -10.0),
                  ("2026-03-01", 2800.0, 3.7)],
            sql="", semantic_sql=None, rewritten=None)
        spec = app.choose_chart(result.ir, result.columns, result.rows, types={"month": "month"})
        units = {"total_net_sales": "usd"}  # pct_change has no unit -> mixed scale -> split

        for kind in ("area", "line"):
            st = _CaptureSt()
            app.render_chart(st, kind, spec, result, units, {}, {"month": "month"}, None)
            self.assertEqual(len(st.charts), 2, f"{kind}: one panel per metric")
            self.assertEqual(len(st.captions), 2, f"{kind}: one caption per metric")
            # the pct_change panel (2nd metric) gets a % axis; the USD panel does not
            self.assertIn("+ '%'", json.dumps(st.charts[1].to_dict()), kind)
            self.assertNotIn("+ '%'", json.dumps(st.charts[0].to_dict()), kind)

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

    def test_vertical_grouped_bar_is_vertical_and_chronological(self):
        cmp = Comparison.from_dict({
            "metric": "total_amount", "split_by": "month",
            "period_field": "classification", "periods": ["Revenue", "Expense"],
        })
        cols = ["month", "total_amount_Revenue", "total_amount_Expense"]
        long = app.comparison_long(cmp, cols, [("2026-04", 120.0, 90.0),
                                               ("2026-03", 100.0, 80.0)])
        spec = app.vertical_grouped_bar(
            long, "month", "classification", ["Revenue", "Expense"],
            fmt="$,.2f", x_type="month",
        ).to_dict()
        enc = spec["encoding"]
        self.assertEqual(enc["x"]["field"], "month")          # category on x -> vertical
        self.assertEqual(enc["xOffset"]["field"], "period")   # side-by-side, not stacked
        self.assertEqual(enc["y"]["axis"]["format"], "$,.2f")
        self.assertEqual(enc["color"]["scale"]["range"], [plots.SERIES_1, plots.SERIES_2])
        # month labels friendly + chronological (not value-sorted)
        self.assertEqual(enc["x"]["sort"], ["Mar 2026", "Apr 2026"])

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
        self.assertEqual(plots.month_label("2026-04-01"), "Apr 2026")  # date_trunc form
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

    def test_display_frame_exclusive_split_is_long_not_zero_filled(self):
        # accounts belong to exactly one classification -> tidy long rows, no 0 columns
        cmp = Comparison.from_dict({"metric": "total_amount", "split_by": "account_name",
                                    "period_field": "classification",
                                    "periods": ["Revenue", "Expense"]})
        result = SimpleNamespace(
            ir=cmp,
            columns=["account_name", "total_amount_Revenue", "total_amount_Expense"],
            rows=[("Product Sales", 527671.30, 0), ("Payroll Expense", 0, 232175.42)])
        df = app._display_frame(result)
        self.assertEqual(list(df.columns), ["account_name", "classification", "total_amount"])
        self.assertEqual(list(df["classification"]), ["Revenue", "Expense"])
        self.assertEqual(list(df["total_amount"]), [527671.30, 232175.42])  # no zeros

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

    def test_render_shows_interpreted_as_when_rewritten(self):
        from text2sql.engine.ir import SemanticQuery

        class _Ctx:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _FakeSt:
            def __init__(self): self.captions = []
            def caption(self, text): self.captions.append(text)
            def columns(self, spec):
                return [self] * (spec if isinstance(spec, int) else len(spec))
            def expander(self, *a, **k): return _Ctx()
            def __getattr__(self, name):  # markdown/dataframe/altair_chart/code/... no-op
                return lambda *a, **k: None

        result = SimpleNamespace(
            ir=SemanticQuery(metrics=["total_net_sales"], dimensions=["market"]),
            columns=["market", "total_net_sales"], rows=[("Houston", 100.0), ("Dallas", 80.0)],
            sql="SELECT ...", semantic_sql=None,
            rewritten="revenue of the past 6 days for Contoso SAS",
        )
        st = _FakeSt()
        app._render_assistant(st, {"result": result, "summary": "hi"})
        self.assertTrue(any(c == "Interpreted as: revenue of the past 6 days for Contoso SAS"
                            for c in st.captions))

    def test_render_chart_honors_selected_kind_override(self):
        from text2sql.engine.ir import SemanticQuery

        class _CaptureSt:
            def __init__(self): self.charts = []
            def altair_chart(self, chart, **k): self.charts.append(chart)
            def bar_chart(self, *a, **k): self.charts.append("bar_chart")
            def caption(self, *a, **k): pass
            def columns(self, spec): return [self] * (spec if isinstance(spec, int) else len(spec))
            def metric(self, *a, **k): pass

        result = SimpleNamespace(
            ir=SemanticQuery(metrics=["total_net_sales"], dimensions=["month"]),
            columns=["month", "total_net_sales"],
            rows=[("2026-01-01", 3.0), ("2026-02-01", 5.0), ("2026-03-01", 4.0)],
            sql="", semantic_sql=None, rewritten=None)
        spec = app.choose_chart(result.ir, result.columns, result.rows,
                                types={"month": "month"})
        self.assertEqual(spec.kind, "line")  # the recommended default

        # native kind -> a line
        st = _CaptureSt()
        app.render_chart(st, "line", spec, result, {"total_net_sales": "usd"}, {},
                         {"month": "month"}, None)
        self.assertIn("line", _marks(st.charts[0].to_dict()))

        # override to area -> an area mark
        st = _CaptureSt()
        app.render_chart(st, "area", spec, result, {"total_net_sales": "usd"}, {},
                         {"month": "month"}, None)
        self.assertIn("area", _marks(st.charts[0].to_dict()))

        # override to bar -> the simple vertical bar fallback (st.bar_chart)
        st = _CaptureSt()
        app.render_chart(st, "bar", spec, result, {"total_net_sales": "usd"}, {},
                         {"month": "month"}, None)
        self.assertEqual(st.charts, ["bar_chart"])

    def test_missing_dimensions_flags_dropped_breakdown(self):
        model = SimpleNamespace(dimensions=[
            SimpleNamespace(name="account_name", synonyms=["account"]),
            SimpleNamespace(name="month", synonyms=["month"]),
            SimpleNamespace(name="classification", synonyms=["type"]),
        ])
        ir = SimpleNamespace(dimensions=["month", "classification"])  # account dropped
        q = "compare revenue and expenses broken down by account and split by month"
        self.assertEqual(app.missing_dimensions(q, ir, model), ["account_name"])

    def test_missing_dimensions_matches_across_punctuation(self):
        # "account," (comma-adjacent) must still match the account dimension
        model = SimpleNamespace(dimensions=[
            SimpleNamespace(name="account_name", synonyms=["account"]),
        ])
        ir = SimpleNamespace(dimensions=["week_start", "classification"])
        q = "side-by-side comparison, broken down by account, including the % of change."
        self.assertEqual(app.missing_dimensions(q, ir, model), ["account_name"])

    def test_missing_dimensions_none_when_present_or_unmentioned(self):
        model = SimpleNamespace(dimensions=[
            SimpleNamespace(name="account_name", synonyms=["account"]),
            SimpleNamespace(name="month", synonyms=["month"]),
        ])
        # account is present in the result -> not flagged
        ir = SimpleNamespace(dimensions=["account_name", "month"])
        self.assertEqual(app.missing_dimensions("revenue by account by month", ir, model), [])
        # account not mentioned -> not flagged (whole-word, no "accounting" false match)
        ir2 = SimpleNamespace(dimensions=["month"])
        self.assertEqual(app.missing_dimensions("revenue by month", ir2, model), [])

    def test_missing_dimensions_skips_time_grains(self):
        # "by month" must not flag the "month" dim as missing when another time grain
        # is present — the planner picks one grain, that's not a dropped breakdown.
        model = SimpleNamespace(dimensions=[
            SimpleNamespace(name="month", synonyms=["month"], type="month"),
            SimpleNamespace(name="txn_month", synonyms=[], type="month"),
        ])
        ir = SimpleNamespace(dimensions=["txn_month"])
        self.assertEqual(app.missing_dimensions("revenue by month", ir, model), [])

    def test_missing_dimensions_reads_comparison_ir(self):
        model = SimpleNamespace(dimensions=[
            SimpleNamespace(name="account_name", synonyms=["account"]),
            SimpleNamespace(name="month", synonyms=[]),
            SimpleNamespace(name="classification", synonyms=[]),
        ])
        cmp = Comparison.from_dict({"metric": "total_amount", "split_by": "month",
                                    "period_field": "classification",
                                    "periods": ["Revenue", "Expense"]})
        # present dims for a Comparison = {split_by, period_field}; account is missing
        self.assertEqual(app.missing_dimensions("compare by account", cmp, model),
                         ["account_name"])

    def test_reset_session_clears_history_and_remints_thread(self):
        state = {"history": [{"role": "user", "text": "hi"}], "thread_id": "old-thread",
                 "dataset": "sales"}
        app.reset_session(state)
        self.assertEqual(state["history"], [])
        self.assertNotEqual(state["thread_id"], "old-thread")
        self.assertEqual(len(state["thread_id"]), 32)  # uuid4().hex
        self.assertEqual(state["dataset"], "sales")  # dataset preserved

    def test_reset_session_mints_distinct_threads(self):
        a, b = {}, {}
        app.reset_session(a)
        app.reset_session(b)
        self.assertNotEqual(a["thread_id"], b["thread_id"])

    def test_record_turn_maps_payload_to_store(self):
        from text2sql.trace.usage import LlmCall
        captured = {}

        class FakeStore:
            def record_turn(self, **kw):
                captured.update(kw)

        result = SimpleNamespace(rewritten="net sales in 2026", semantic_sql="SELECT ...",
                                 sql="SELECT compiled", rows=[(1,), (2,), (3,)])
        payload = {"result": result, "summary": "s", "chart_kind": "line"}
        calls = [LlmCall("plan", "opus", 100, 20)]
        app.record_turn(FakeStore(), "thread-abc", "sales", "how were sales?",
                        payload, calls, 250.0)
        self.assertEqual(captured["thread_id"], "thread-abc")
        self.assertEqual(captured["dataset"], "sales")
        self.assertEqual(captured["rewritten"], "net sales in 2026")
        self.assertEqual(captured["semantic_sql"], "SELECT ...")
        self.assertEqual(captured["sql"], "SELECT compiled")
        self.assertEqual(captured["row_count"], 3)
        self.assertEqual(captured["chart_kind"], "line")
        self.assertIsNone(captured["error"])
        self.assertEqual(captured["latency_ms"], 250.0)
        self.assertEqual(captured["calls"], calls)

    def test_record_turn_on_error_payload(self):
        captured = {}

        class FakeStore:
            def record_turn(self, **kw):
                captured.update(kw)

        payload = {"error": "Sorry — nope"}  # no result
        app.record_turn(FakeStore(), "t", "sales", "q", payload, [], 5.0)
        self.assertEqual(captured["error"], "Sorry — nope")
        self.assertIsNone(captured["row_count"])
        self.assertIsNone(captured["sql"])
        self.assertIsNone(captured["chart_kind"])

    def test_horizontal_bar_signed_metric_uses_axis_and_zero_rule(self):
        df = app.to_frame(["classification", "net_income"],
                          [("Revenue", 3386572.8), ("Expense", -2709258.24)])
        spec = plots.horizontal_bar(df, "classification", "net_income", fmt="$,.2f").to_dict()
        marks = _marks(spec)
        self.assertIn("rule", marks)      # a zero baseline for the diverging bars
        self.assertNotIn("text", marks)   # no direct labels (they'd overlap a diverging bar)
        bar = next(lyr for lyr in spec["layer"]
                   if (lyr["mark"]["type"] if isinstance(lyr["mark"], dict) else lyr["mark"]) == "bar")
        self.assertEqual(bar["encoding"]["x"]["axis"]["format"], "$,.2f")  # values via the axis

    def test_horizontal_bar_positive_keeps_direct_labels(self):
        df = app.to_frame(["p", "v"], [("A", 5.0), ("B", 3.0)])
        self.assertEqual(_marks(plots.horizontal_bar(df, "p", "v").to_dict()), {"bar", "text"})

    def test_horizontal_bar_story_titles_and_greys_non_focus(self):
        from text2sql.chat.story import StorySpec
        df = app.to_frame(["product_name", "units_sold"],
                          [("Cappuccino", 4983), ("Americano", 3491)])
        story = StorySpec(title="Cappuccino leads — 4,983", emphasis="Cappuccino")
        spec = app.horizontal_bar(df, "product_name", "units_sold", story=story).to_dict()
        self.assertEqual(spec["title"]["text"], "Cappuccino leads — 4,983")
        # colour is now a focus-vs-muted condition, not a flat value
        self.assertIn("condition", spec["layer"][0]["encoding"]["color"])

    def test_line_chart_story_adds_title_reference_and_callouts(self):
        from text2sql.chat.story import Annotation, Reference, StorySpec
        df = app.to_frame(["month", "total_net_sales"],
                          [("Jan 2026", 4000.0), ("Jun 2026", 4600.0), ("Dec 2026", 2918.0)])
        story = StorySpec(
            title="Net sales fell 27%", subtitle="$4,000 → $2,918",
            references=[Reference(3839.0, "avg", "avg")],
            annotations=[Annotation("Dec 2026", 2918.0, "$2,918", "latest"),
                         Annotation("Jun 2026", 4600.0, "peak · Jun 2026", "peak")])
        spec = app.line_chart(df, "month", "total_net_sales", story=story).to_dict()
        self.assertEqual(spec["title"]["text"], "Net sales fell 27%")
        # average rule + the line + latest point + text callouts
        self.assertTrue({"rule", "line", "point", "text"} <= _marks(spec), _marks(spec))

    def test_safe_summarize_falls_back_on_error(self):
        class Boom:
            def summarize(self, *a):
                raise RuntimeError("no key")

        out = app.safe_summarize(Boom(), "q", ["a"], [(1,)])
        self.assertIn("1 row", out)


if __name__ == "__main__":
    unittest.main()
