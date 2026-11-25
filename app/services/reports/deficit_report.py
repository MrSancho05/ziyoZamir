"""📥 Tanqis (yoki barcha) mahsulotlar hisoboti: Excel (.xlsx) va PDF.

Ustunlar: № | Наименование товара | Категория | Ед. изм. | Остаток | Мин. остаток | Yetishmayotgan miqdor
(yetishmayotgan = Мин. остаток − Остаток, manfiy bo'lsa 0). Ro'yxat foydalanuvchi ekranda ko'rgan ro'yxatning
o'zi (UserStore), kategoriya va birlik katalogdan (Остатки и Расходы) qo'shiladi."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from app.models import DeficitItem
from app.services.categories import label as cat_label
from app.services.reports.pdf import FONT, FONT_BOLD, _pdf_safe


@dataclass
class ReportRow:
    name: str
    category: str
    unit: str
    stock: float
    min_stock: float

    @property
    def short(self) -> float:
        return max(0.0, self.min_stock - self.stock)


def build_rows(items: Sequence[DeficitItem], catalog: Iterable, lang: str) -> List[ReportRow]:
    by_name: Dict[str, object] = {p.name.strip().casefold(): p for p in catalog}
    rows = []
    for it in items:
        p = by_name.get(it.name.strip().casefold())
        category = cat_label(p.category, lang) if p is not None and p.category else "—"
        rows.append(ReportRow(name=it.name, category=_pdf_safe(category) or category, unit=getattr(p, "unit", "") or "—",
                              stock=it.stock, min_stock=it.norm))
    # eng ko'p yetishmaydigani birinchi
    rows.sort(key=lambda r: (-r.short, r.name.casefold()))
    return rows


def _num(x: float):
    return int(x) if float(x).is_integer() else round(x, 2)


def _headers(t: Dict[str, str]) -> List[str]:
    return [t["rep_col_num"], t["rep_col_name"], t["rep_col_category"], t["rep_col_unit"], t["rep_col_stock"],
            t["rep_col_min"], t["rep_col_short"]]


def build_xlsx(rows: Sequence[ReportRow], t: Dict[str, str], title: str, tz: Optional[ZoneInfo]) -> bytes:
    from openpyxl import Workbook                      # faqat kerak bo'lganda (bot ishga tushishi bunga bog'liq emas)
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = t["rep_sheet_name"][:31]
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M") if tz else datetime.now().strftime("%d.%m.%Y %H:%M")
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14, color="1F3A5F")
    ws["A2"] = f"{t['rep_generated']}: {now} · {t['rep_total']}: {len(rows)}"
    ws["A2"].font = Font(italic=True, color="6B7785")

    head = _headers(t)
    thin = Side(style="thin", color="D5DCE4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c, text in enumerate(head, start=1):
        cell = ws.cell(row=4, column=c, value=text)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3A5F")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    red = Font(bold=True, color="C62828")
    zebra = PatternFill("solid", fgColor="F2F6FA")
    for i, r in enumerate(rows, start=1):
        values = [i, r.name, r.category, r.unit, _num(r.stock), _num(r.min_stock), _num(r.short)]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=4 + i, column=c, value=v)
            cell.border = border
            if i % 2 == 0:
                cell.fill = zebra
            if c >= 5:
                cell.number_format = "#,##0.##"
                cell.alignment = Alignment(horizontal="right")
            if c in (1, 4):
                cell.alignment = Alignment(horizontal="center")
        if r.short > 0:
            ws.cell(row=4 + i, column=7).font = red
    for c, width in enumerate((6, 42, 24, 10, 12, 14, 16), start=1):
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.row_dimensions[4].height = 30
    ws.freeze_panes = "A5"
    if rows:
        ws.auto_filter.ref = f"A4:G{4 + len(rows)}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_pdf(rows: Sequence[ReportRow], t: Dict[str, str], title: str, tz: Optional[ZoneInfo]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M") if tz else datetime.now().strftime("%d.%m.%Y %H:%M")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=28, rightMargin=28, topMargin=32, bottomMargin=36,
                            title=_pdf_safe(title))
    h1 = ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=15, leading=19, textColor=colors.HexColor("#1F3A5F"))
    sub = ParagraphStyle("sub", fontName=FONT, fontSize=9, leading=12, textColor=colors.HexColor("#6B7785"))
    cell = ParagraphStyle("cell", fontName=FONT, fontSize=8.5, leading=10.5)
    head = ParagraphStyle("head", fontName=FONT_BOLD, fontSize=8.5, leading=10.5, textColor=colors.white,
                          alignment=1)

    def p(text, style=cell):
        return Paragraph(_pdf_safe(str(text)).replace("&", "&amp;").replace("<", "&lt;"), style)

    data = [[p(h, head) for h in _headers(t)]]
    for i, r in enumerate(rows, start=1):
        data.append([i, p(r.name), p(r.category), r.unit, _fmt(r.stock), _fmt(r.min_stock), _fmt(r.short)])
    table = Table(data, colWidths=[22, 185, 95, 40, 55, 60, 82], repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, -1), FONT, 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(rows, start=1):
        if r.short > 0:
            style += [("TEXTCOLOR", (6, i), (6, i), colors.HexColor("#C62828")), ("FONT", (6, i), (6, i), FONT_BOLD, 8.5)]
    table.setStyle(TableStyle(style))

    def footer(canvas, _doc):
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(colors.HexColor("#6B7785"))
        canvas.drawString(28, 20, f"Ziyo Zamir MCHJ · {now}")
        canvas.drawRightString(A4[0] - 28, 20, str(canvas.getPageNumber()))

    doc.build([Paragraph(_pdf_safe(title), h1), Spacer(1, 4),
               Paragraph(f"{t['rep_generated']}: {now} · {t['rep_total']}: {len(rows)}", sub), Spacer(1, 10), table],
              onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _fmt(x: float) -> str:
    v = _num(x)
    return f"{v:,}".replace(",", " ") if isinstance(v, int) else f"{v:g}"
