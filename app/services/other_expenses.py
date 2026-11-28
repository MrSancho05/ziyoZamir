"""🧾 Boshqa xarajatlar — ombor tovarlariga aloqasi yo'q xarajatlar (benzin, ofis, transport, ta'mirlash ...).

Alohida Google Sheets varag'i «Прочие расходы»: Дата | Категория | Описание | Сумма (сум) | Ответственный.
Varaq bo'lmasa — bot o'zi yaratadi. Ombor qiymati / Приход / Расход hisoblariga aralashmaydi."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from gspread.exceptions import WorksheetNotFound
from gspread.utils import ValueInputOption, ValueRenderOption, rowcol_to_a1

from app.services import cache_bus
from app.services.sheets import SheetsClient
from app.services.warehouse import _cell, _find_col, _header_index, clean_text, num_out, parse_number, to_date

logger = logging.getLogger(__name__)

HEADERS = ("Дата", "Категория", "Описание", "Сумма (сум)", "Ответственный")
# (sheetga yoziladigan nom, uz tugmasi, ru tugmasi)
DEFAULT_CATEGORIES: Tuple[Tuple[str, str, str], ...] = (
    ("Топливо / Бензин", "⛽ Yoqilg'i / Benzin", "⛽ Топливо / Бензин"),
    ("Офис / Помещение", "🏢 Xona / Ofis jihozlari", "🏢 Помещение / Офис"),
    ("Транспорт / Грузы", "🚚 Transport / Yuk", "🚚 Транспорт / Грузы"),
    ("Ремонт", "🔧 Ta'mirlash", "🔧 Ремонт"),
    ("Прочее", "📌 Boshqa", "📌 Прочее"),
)
CACHE_TTL = 60.0


def category_label(name: str, lang: str) -> str:
    for canon, uz, ru in DEFAULT_CATEGORIES:
        if canon.casefold() == str(name).strip().casefold():
            return ru if lang == "ru" else uz
    return f"🧾 {name}" if name else "🧾 —"


@dataclass
class OtherExpense:
    day: date
    category: str
    description: str
    amount: float
    who: str
    row: int = 0                 # varaqdagi qator raqami (1 dan)

    def expect(self) -> Dict[str, object]:
        return {"row": self.row, "iso": self.day.isoformat(), "amount": self.amount, "description": self.description}


@dataclass
class OtherSummary:
    total: float
    count: int
    by_category: Dict[str, float]
    rows: List[OtherExpense]


class OtherExpensesService:
    def __init__(self, sheets: SheetsClient, tab: str, tz) -> None:
        self._sheets = sheets
        self.tab = tab
        self._tz = tz
        self._rows: List[OtherExpense] = []
        self._ts = 0.0

    def _ws(self):
        """Varaq bo'lmasa yaratiladi va sarlavhalar yoziladi."""
        ss = self._sheets.spreadsheet
        try:
            ws = ss.worksheet(self.tab)
        except WorksheetNotFound:
            ws = ss.add_worksheet(self.tab, rows=500, cols=len(HEADERS))
            ws.update([list(HEADERS)], "A1:E1", value_input_option=ValueInputOption.raw)
            try:
                ss.batch_update({"requests": [
                    {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                                    "fields": "userEnteredFormat.textFormat.bold"}},
                    {"updateSheetProperties": {"properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                                               "fields": "gridProperties.frozenRowCount"}}]})
            except Exception:
                logger.warning("«%s» sarlavhasini formatlab bo'lmadi", self.tab, exc_info=True)
            logger.info("«%s» varag'i yaratildi", self.tab)
        return ws

    @staticmethod
    def _cols(header) -> Dict[str, Optional[int]]:
        return {"date": _find_col(header, "Дата"), "cat": _find_col(header, "Категория"),
                "desc": _find_col(header, "Описание"), "sum": _find_col(header, "Сумма (сум)", "сумма"),
                "who": _find_col(header, "Ответственный", "ответствен")}

    def _read_sync(self) -> List[OtherExpense]:
        ws = self._ws()
        values = ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
        if not values:
            return []
        h = _header_index(values, "Категория", "Сумма")
        c = self._cols(values[h])
        out = []
        year = date.today().year
        for i, row in enumerate(values[h + 1:], start=h + 2):
            amount = parse_number(_cell(row, c["sum"]))
            day = to_date(_cell(row, c["date"]), year)
            if not amount or day is None:
                continue
            out.append(OtherExpense(day=day, category=str(_cell(row, c["cat"])).strip(),
                                    description=str(_cell(row, c["desc"])).strip(), amount=amount,
                                    who=str(_cell(row, c["who"])).strip(), row=i))
        return out

    def reset_cache(self) -> None:
        self._ts = 0.0

    def _locate(self, expect: Dict[str, object]) -> Tuple[object, OtherExpense]:
        ws = self._ws()
        rows = self._read_sync()
        def same(r: OtherExpense) -> bool:
            return (r.day.isoformat() == expect.get("iso") and abs(r.amount - float(expect.get("amount") or 0)) < 1e-6
                    and r.description == expect.get("description"))
        hit = next((r for r in rows if r.row == int(expect.get("row") or 0) and same(r)), None)
        if hit is None:
            matches = [r for r in rows if same(r)]
            if len(matches) != 1:
                raise LookupError("other expense row moved")
            hit = matches[0]
        return ws, hit

    def _delete_sync(self, expect: Dict[str, object]) -> OtherExpense:
        ws, r = self._locate(expect)
        self._sheets.spreadsheet.batch_update({"requests": [{"deleteDimension": {"range": {
            "sheetId": ws.id, "dimension": "ROWS", "startIndex": r.row - 1, "endIndex": r.row}}}]})
        return r

    async def delete(self, expect: Dict[str, object]) -> OtherExpense:
        r = await self._sheets.run(self._delete_sync, expect)
        self._ts = 0.0
        cache_bus.invalidate_all()
        return r

    def _update_sync(self, expect: Dict[str, object], fields: Dict[str, object]) -> OtherExpense:
        """fields: amount, category, description, iso."""
        ws, r = self._locate(expect)
        values = ws.get_all_values()
        h = _header_index(values, "Категория", "Сумма") if values else 0
        c = self._cols(values[h] if values else HEADERS)
        data = []
        for key, col, conv in (("iso", "date", str), ("category", "cat", str),
                               ("description", "desc", lambda v: clean_text(str(v), 200)),
                               ("amount", "sum", lambda v: num_out(float(v)))):
            if key in fields and c.get(col) is not None:
                data.append({"range": rowcol_to_a1(r.row, c[col] + 1), "values": [[conv(fields[key])]]})
        if data:
            ws.batch_update(data, value_input_option=ValueInputOption.user_entered)
        return r

    async def update(self, expect: Dict[str, object], fields: Dict[str, object]) -> OtherExpense:
        r = await self._sheets.run(self._update_sync, expect, fields)
        self._ts = 0.0
        cache_bus.invalidate_all()
        return r

    async def rows(self, force: bool = False) -> List[OtherExpense]:
        if force or time.monotonic() - self._ts > CACHE_TTL:
            self._rows = await self._sheets.run(self._read_sync)
            self._ts = time.monotonic()
        return self._rows

    async def ensure_sheet(self) -> None:
        await self._sheets.run(self._ws)

    def _add_sync(self, day: date, category: str, description: str, amount: float, who: str) -> int:
        ws = self._ws()
        values = ws.get_all_values()
        h = _header_index(values, "Категория", "Сумма") if values else 0
        c = self._cols(values[h] if values else HEADERS)
        c = {k: (v if v is not None else i) for i, (k, v) in enumerate(c.items())}
        last = max([h] + [i for i, r in enumerate(values) if i > h and any(str(x).strip() for x in r)])
        target = last + 2
        if ws.row_count < target:
            ws.add_rows(50)
        data = [{"range": rowcol_to_a1(target, c[k] + 1), "values": [[v]]} for k, v in (
            ("date", day.isoformat()), ("cat", category), ("desc", clean_text(description, 200)),
            ("sum", num_out(amount)), ("who", who))]
        ws.batch_update(data, value_input_option=ValueInputOption.user_entered)
        return target

    async def add(self, day: date, category: str, description: str, amount: float, who: str) -> int:
        row = await self._sheets.run(self._add_sync, day, category, description, amount, who)
        self._ts = 0.0
        cache_bus.invalidate_all()
        return row

    async def summary(self, start: date, end: date) -> OtherSummary:
        rows = [r for r in await self.rows() if start <= r.day <= end]
        by_cat: Dict[str, float] = {}
        for r in rows:
            by_cat[r.category or "—"] = by_cat.get(r.category or "—", 0.0) + r.amount
        return OtherSummary(total=sum(r.amount for r in rows), count=len(rows),
                            by_category=dict(sorted(by_cat.items(), key=lambda kv: -kv[1])), rows=rows)
