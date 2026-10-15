import csv
import io

from app.models import DeficitItem
from app.utils.formatting import fmt_num


def build_deficit_csv(items: list[DeficitItem], t: dict[str, str]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([t["pdf_title"]])
    writer.writerow([f"{t['total_count']}: {len(items)}"])
    writer.writerow([])
    writer.writerow([t["col_num"], t["col_name"], t["col_norm"], t["col_stock"]])

    for idx, item in enumerate(items, 1):
        writer.writerow([idx, item.name, fmt_num(item.norm), fmt_num(item.stock)])

    # utf-8-sig: Excel kirill harflarini to'g'ri ochishi uchun
    return output.getvalue().encode("utf-8-sig")
