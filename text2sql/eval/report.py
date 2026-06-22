"""Render a Report as a readable text table with a summary footer."""

from __future__ import annotations

from .runner import Report


def format_report(report: Report) -> str:
    lines = []
    header = f"{'CASE':<26} {'EXEC':<6} {'IR':<6} NOTES"
    lines.append(header)
    lines.append("-" * len(header))
    for r in report.results:
        exec_mark = "PASS" if r.passed else "FAIL"
        ir_mark = "exact" if r.exact_ir else "~"
        note = ""
        if r.error:
            note = r.error
        elif not r.passed:
            note = "rows differ from expected"
        elif not r.exact_ir and r.ir_score:
            s = r.ir_score
            note = (
                f"metric f1={s.metrics.f1:.2f} "
                f"dim f1={s.dimensions.f1:.2f} "
                f"filter f1={s.filters.f1:.2f}"
            )
        if len(note) > 80:
            note = note[:77] + "..."
        lines.append(f"{r.id:<26} {exec_mark:<6} {ir_mark:<6} {note}")

    lines.append("-" * len(header))
    lines.append(
        f"execution accuracy: {report.n_passed}/{report.total} "
        f"({report.exec_accuracy:.0%})   "
        f"exact-IR: {report.exact_ir_rate:.0%}"
    )
    lines.append(
        f"mean component f1 — metrics {report.mean_metric_f1:.2f}, "
        f"dimensions {report.mean_dimension_f1:.2f}, "
        f"filters {report.mean_filter_f1:.2f}"
    )
    return "\n".join(lines)
