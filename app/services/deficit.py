from typing import Any

from app.models import DeficitItem
from app.services.sheets import SheetsClient

COL_NAME = "Наименование товара"
COL_STOCK = "Остаток на складе"
COL_NORM = "Мин. норматив (Минимальный остаток)"
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


def _parse_row(row: dict[str, Any]) -> DeficitItem | None:
    stock = _to_number(row.get(COL_STOCK))
    norm = _to_number(row.get(COL_NORM))
    status = str(row.get(COL_STATUS, "")).strip().lower()

    is_deficit = stock < norm or (status != "" and IN_STOCK_MARK not in status)
    if not is_deficit:
        return None

    name = str(row.get(COL_NAME, "")).strip() or "Noma'lum"
    return DeficitItem(name=name, stock=stock, norm=norm)


async def load_deficit_items(sheets: SheetsClient) -> list[DeficitItem]:
    records = await sheets.get_records()
    items = (_parse_row(row) for row in records)
    return [item for item in items if item is not None]
