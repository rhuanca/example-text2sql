"""Turn a SemanticModel into a friendly star-schema diagram (Graphviz DOT) plus
small helpers for a "click a table" inspector panel.

Pure and importable (no Streamlit, no I/O) so the DOT generation is unit-tested.
The Streamlit rendering that consumes this lives in app.py.
"""

from __future__ import annotations

from ..semantic.model import SemanticModel

# Fact tables (those carrying metrics) vs dimension tables get different colors
# so the star shape reads at a glance.
FACT_COLOR = "#F6C453"  # amber
DIM_COLOR = "#7EA6E0"  # blue
_MAX_FIELDS_IN_NODE = 6


def classify_tables(model: SemanticModel) -> dict[str, str]:
    """Map each logical table name to 'fact' (has metrics) or 'dim'."""
    metric_tables = {m.table for m in model.metrics}
    return {t.name: ("fact" if t.name in metric_tables else "dim") for t in model.tables}


def table_fields(model: SemanticModel, table_name: str) -> dict:
    """Metrics / dimensions / join-key facts declared on one logical table."""
    return {
        "metrics": [m for m in model.metrics if m.table == table_name],
        "dimensions": [d for d in model.dimensions if d.table == table_name],
        "facts": [f for f in model.facts if f.table == table_name],
    }


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _field_line(label: str, names: list[str]) -> str:
    if not names:
        return ""
    shown = names[:_MAX_FIELDS_IN_NODE]
    extra = len(names) - len(shown)
    text = ", ".join(shown) + (f"  (+{extra} more)" if extra else "")
    return (
        f'<tr><td align="left"><font point-size="9">'
        f"<b>{label}</b> {_esc(text)}</font></td></tr>"
    )


def _node_label(model: SemanticModel, table, kind: str) -> str:
    fields = table_fields(model, table.name)
    color = FACT_COLOR if kind == "fact" else DIM_COLOR
    rows = [
        f'<tr><td bgcolor="{color}"><b>{_esc(table.name)}</b>'
        f'  <font point-size="9">({kind.upper()})</font></td></tr>',
        f'<tr><td align="left"><font point-size="8" color="#666">'
        f"{_esc(table.table)}</font></td></tr>",
    ]
    metric_line = _field_line("metrics:", [m.name for m in fields["metrics"]])
    if metric_line:
        rows.append(metric_line)
    dim_line = _field_line("fields:", [d.name for d in fields["dimensions"]])
    if dim_line:
        rows.append(dim_line)
    inner = "".join(rows)
    return (
        '<<table border="0" cellborder="1" cellspacing="0" cellpadding="6">'
        f"{inner}</table>>"
    )


def model_to_dot(model: SemanticModel) -> str:
    """Render the semantic model as a Graphviz DOT string: one node per logical
    table (fact vs dim colored), one edge per relationship labeled with its join
    keys. Consumed by st.graphviz_chart, which renders it client-side."""
    kinds = classify_tables(model)
    lines = [
        "digraph semantic_model {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        '  node [shape=plaintext, fontname="Helvetica"];',
        '  edge [fontname="Helvetica", color="#8899aa", fontcolor="#556"];',
    ]
    for t in model.tables:
        label = _node_label(model, t, kinds[t.name])
        lines.append(f"  {t.name} [label={label}];")
    for r in model.relationships:
        join = " AND ".join(f"{fc} = {tc}" for fc, tc in r.column_pairs)
        lines.append(
            f'  {r.from_table} -> {r.to_table} '
            f'[label="{_esc(join)}", fontsize=9, arrowhead=none];'
        )
    lines.append("}")
    return "\n".join(lines)
