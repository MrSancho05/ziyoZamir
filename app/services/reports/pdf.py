import io
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.models import DeficitItem
from app.utils.formatting import fmt_num

FONT = "AppFont"
FONT_BOLD = "AppFont-Bold"


def register_fonts(fonts_dir: Path) -> None:
    """Dastur ishga tushganda BIR marta chaqiriladi."""
    regular_candidates = [
        fonts_dir / "DejaVuSans.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    bold_candidates = [
        fonts_dir / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]

    regular = next((p for p in regular_candidates if p.exists()), None)
    if regular is None:
        raise RuntimeError("Kirill shrifti topilmadi! assets/fonts/DejaVuSans.ttf ni qo'shing.")
    bold = next((p for p in bold_candidates if p.exists()), regular)

    pdfmetrics.registerFont(TTFont(FONT, str(regular)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))


def build_deficit_pdf(items: list[DeficitItem], t: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    c_header = colors.HexColor("#1F3A5F")
    c_zebra = colors.HexColor("#F2F6FA")
    c_line = colors.HexColor("#D5DCE4")
    c_text = colors.HexColor("#222222")
    c_muted = colors.HexColor("#6B7785")
    c_red = colors.HexColor("#C62828")

    left, right = 36, page_w - 36
    bottom = 50
    fs, line_h, pad = 10, 13, 8
    x_num = left + 18
    x_name = left + 44
    x_norm = right - 105
    x_stock = right - 38
    name_w = (x_norm - 40) - x_name

    page_no = 1
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    def draw_footer() -> None:
        c.setStrokeColor(c_line)
        c.line(left, 38, right, 38)
        c.setFont(FONT, 8)
        c.setFillColor(c_muted)
        c.drawString(left, 26, now)
        c.drawRightString(right, 26, str(page_no))

    def draw_table_header(y_top: float) -> float:
        h = 24
        c.setFillColor(c_header)
        c.rect(left, y_top - h, right - left, h, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(FONT_BOLD, fs)
        base = y_top - 16
        c.drawCentredString(x_num, base, t["col_num"])
        c.drawString(x_name, base, t["col_name"])
        c.drawCentredString(x_norm, base, t["col_norm"])
        c.drawCentredString(x_stock, base, t["col_stock"])
        return y_top - h

    c.setFillColor(c_header)
    c.setFont(FONT_BOLD, 16)
    c.drawString(left, page_h - 50, t["pdf_title"])
    c.setFillColor(c_muted)
    c.setFont(FONT, 10)
    c.drawString(left, page_h - 70, f"{t['total_count']}: {len(items)}    |    {now}")

    y = draw_table_header(page_h - 90)

    for idx, item in enumerate(items, 1):
        norm = fmt_num(item.norm)
        stock = fmt_num(item.stock)

        lines = simpleSplit(item.name, FONT, fs, name_w) or [""]
        row_h = len(lines) * line_h + pad

        if y - row_h < bottom:
            draw_footer()
            c.showPage()
            page_no += 1
            y = draw_table_header(page_h - 40)

        if idx % 2 == 0:
            c.setFillColor(c_zebra)
            c.rect(left, y - row_h, right - left, row_h, fill=1, stroke=0)

        c.setStrokeColor(c_line)
        c.setLineWidth(0.5)
        c.line(left, y - row_h, right, y - row_h)

        base = y - pad / 2 - 9

        c.setFillColor(c_muted)
        c.setFont(FONT, fs)
        c.drawCentredString(x_num, base, str(idx))

        c.setFillColor(c_text)
        for i, line in enumerate(lines):
            c.drawString(x_name, base - i * line_h, line)

        c.setFont(FONT_BOLD if norm != "0" else FONT, fs)
        c.setFillColor(c_text if norm != "0" else c_muted)
        c.drawCentredString(x_norm, base, norm)

        c.setFont(FONT_BOLD, fs)
        c.setFillColor(c_red if stock == "0" else c_text)
        c.drawCentredString(x_stock, base, stock)

        y -= row_h

    draw_footer()
    c.save()
    return buffer.getvalue()
