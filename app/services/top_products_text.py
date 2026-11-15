"""Top mahsulotlar hisobotini (HTML) matnga aylantirish. Matnlar `app/locales/texts.py` da (uz / ru)."""
from html import escape

from app.services.top_products import CLASSES, ProductStat, TopReport
from app.utils.formatting import fmt_num

_ICON = {"A": "🟢", "B": "🟡", "C": "⚪", "D": "🔘"}


def _line(t: dict[str, str], s: ProductStat) -> str:
    line = t["top_line"].format(rank=s.rank, name=escape(s.name), out_qty=fmt_num(s.out_qty),
                                out_cnt=s.out_cnt, in_qty=fmt_num(s.in_qty), in_cnt=s.in_cnt)
    if s.no_inflow:
        line += " ⚠️"
    if s.overstock:
        line += " 📦"
    return line


def render_top(t: dict[str, str], report: TopReport, period_label: str, tabs: dict[str, str]) -> str:
    """Reyting: A -> B -> C -> D, har bir sinf ichida ball bo'yicha kamayish tartibida."""
    lines = [t["top_title"].format(period=period_label),
             t["top_summary"].format(n=len(report.items), out_rows=report.out_rows, in_rows=report.in_rows)]
    for key, tab in (("inflow", tabs.get("inflow", "")), ("outflow", tabs.get("outflow", ""))):
        found = report.in_found if key == "inflow" else report.out_found
        if not found:
            lines.append(t["top_sheet_missing"].format(tab=escape(tab)))
    for key in report.undated:
        lines.append(t["top_undated"].format(tab=escape(tabs.get(key, key))))
    lines.append("")

    for cls in CLASSES:
        group = [s for s in report.items if s.cls == cls]
        if not group:
            continue
        lines.append(t["top_group_head"].format(icon=_ICON[cls], cls=cls, title=t[f"top_cls_{cls}"], n=len(group)))
        lines.append(t[f"top_rec_{cls}"])
        lines += [_line(t, s) for s in group]
        lines.append("")

    lines.append(t["top_legend"])
    if report.weights:
        lines.append(t["top_formula"].format(
            out_qty=round(report.weights.get("out_qty", 0) * 100),
            out_cnt=round(report.weights.get("out_cnt", 0) * 100),
            in_cnt=round(report.weights.get("in_cnt", 0) * 100)))
    return "\n".join(lines).strip()
