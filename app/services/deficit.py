from typing import Any

from app.models import DeficitItem
from app.services.sheets import SheetsClient

COL_NAME = "Наименование товара"
COL_STOCK = "Остаток на складе"
COL_NORM = "Мин. норматив (Минимальный остаток)"
COL_NORM_SHORT = "Мин. остаток"          # yangi sarlavha nomi
COL_STATUS = "Статус"

IN_STOCK_MARK = "в наличии"


def _to_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _is_blank(row: dict[str, Any]) -> bool:
    """Nomi ham, qoldig'i ham, normativi ham, holati ham bo'sh qator (jadvaldagi bo'sh joy)."""
    return not any(str(row.get(c, "")).strip() for c in (COL_NAME, COL_STOCK, COL_NORM, COL_NORM_SHORT, COL_STATUS))


def _parse_product(row: dict[str, Any]) -> DeficitItem | None:
    """Jadval qatorini mahsulotga aylantiradi (tanqis yoki yo'qligi `is_deficit` da). Bo'sh qator -> None."""
    if _is_blank(row):
        return None
    stock = _to_number(row.get(COL_STOCK))
    norm = _to_number(row.get(COL_NORM) if COL_NORM in row else row.get(COL_NORM_SHORT))
    status = str(row.get(COL_STATUS, "")).strip().lower()

    is_deficit = stock < norm or (status != "" and IN_STOCK_MARK not in status)

    name = str(row.get(COL_NAME, "")).strip() or "Noma'lum"
    # Manfiy qiymat (masalan -995) skladda bo'lishi mantiqsiz: ko'rsatishda 0 qilinadi.
    # Tanqis ekanligi esa asl qiymat bo'yicha aniqlangan (yuqorida).
    return DeficitItem(name=name, stock=max(stock, 0.0), norm=max(norm, 0.0), is_deficit=is_deficit)


async def load_all_items(sheets: SheetsClient) -> list[DeficitItem]:
    """Barcha mahsulotlar (tanqis va tanqis emas), bo'sh qatorlarsiz."""
    records = await sheets.get_records()
    items = (_parse_product(row) for row in records)
    return [item for item in items if item is not None]


async def load_deficit_items(sheets: SheetsClient) -> list[DeficitItem]:
    return [item for item in await load_all_items(sheets) if item.is_deficit]
