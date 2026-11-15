import calendar
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from gspread.exceptions import WorksheetNotFound

from app.locales.texts import MONTHS
from app.services.attendance import RED, WHITE, LayoutError, TemplateMissingError, _fill
from app.services.attendance_grid import cell_ref, months_in_range
from app.services.shop_grid import (
    ShopBlock,
    ShopStats,
    block_amount,
    compute_shop_stats,
    find_shop_block,
    find_shop_blocks,
)
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)

GAP_ROWS = 2
YELLOW = {"red": 1.0, "green": 0.95, "blue": 0.4}      # summa kiritilmagan kunlar


class NoBlockError(Exception):
    """Shu oy uchun jadvalda blok yo'q (va uni yaratish mumkin emas)."""


class NotEnteredError(Exception):
    """Kun uchun summa hali kiritilmagan (yoki o'qib bo'lmaydi): qo'shish/ayirish mumkin emas."""


class NegativeResultError(Exception):
    """Ayirgandan keyin summa manfiy bo'lib qolardi."""

    def __init__(self, current: int) -> None:
        super().__init__(current)
        self.current = current


class ShopService:
    """Do'kon xarajatlari: har YIL uchun bitta varaq (masalan 'Магазин 2026'), ichida oylar bloklar bo'lib
    joylashadi. Yakshanba dam olish kuni: jadvalda avtomatik qizil bo'yaladi, summa kiritilmaydi.

    Yangi oy: oxirgi blokning nusxasi pastga qo'yiladi. Yangi yil: oldingi yil varag'ining oxirgi
    bloki bilan yangi varaq ochiladi."""

    def __init__(self, sheets: SheetsClient, tab_format: str, tz: ZoneInfo | None = None) -> None:
        self._sheets = sheets
        self._tab_format = tab_format
        self._tz = tz
        self._painted: set[tuple[int, int]] = set()
        # (yil, oy) -> {kun: kiritilmagan(sariq)mi}; jadvalga oxirgi yozilgan holat
        self._marks: dict[tuple[int, int], dict[int, bool]] = {}

    # ---------------- ommaviy (async) ----------------
    async def get_day(self, day: date) -> tuple[bool, int | None] | None:
        """(kiritilganmi, summa). Blok yo'q bo'lsa None. Hech narsa yaratmaydi."""
        return await self._sheets.run(self._get_day, day)

    async def set_day(self, day: date, amount: int, create: bool) -> None:
        await self._sheets.run(self._set_day, day, amount, create)

    async def adjust_day(self, day: date, delta: int) -> tuple[int, int]:
        """Kiritilgan summaga delta qo'shadi (manfiy bo'lsa ayiradi). (eski, yangi) qaytaradi.
        O'qish va yozish bitta qulf ostida bajariladi."""
        return await self._sheets.run(self._adjust_day, day, delta)

    async def is_entered(self, day: date) -> bool:
        found = await self.get_day(day)
        return bool(found and found[0])

    async def day_statuses(self, days: list[date]) -> dict[date, str]:
        return await self._sheets.run(self._day_statuses, days)

    async def month_total_until(self, day: date) -> int:
        return await self._sheets.run(self._month_total_until, day)

    async def period_stats(self, start: date, end: date) -> ShopStats:
        blocks = await self._sheets.run(self._read_blocks, months_in_range(start, end))
        return compute_shop_stats(blocks, start, end)

    # ---------------- varaq va bloklar (sinxron) ----------------
    def tab_name(self, year: int) -> str:
        return self._tab_format.format(year=year)

    def _find_ws(self, year: int):
        try:
            return self._sheets.spreadsheet.worksheet(self.tab_name(year))
        except WorksheetNotFound:
            return None

    def _get_block(self, year: int, month: int, create: bool):
        ws = self._find_ws(year)
        if ws is None:
            if not create:
                return None
            ws = self._create_year_ws(year, month)
        values = ws.get_all_values()
        block = find_shop_block(values, month)
        if block is None:
            if not create:
                return None
            if not find_shop_blocks(values):
                raise LayoutError()
            return self._append_month_block(ws, values, year, month)
        self._paint_once(ws, block, year, month, reset=False)
        self._sync_colors(ws, block, year, month)
        return ws, values, block

    def _create_year_ws(self, year: int, month: int):
        name = self.tab_name(year)
        template = None
        for back in range(1, 6):
            template = self._find_ws(year - back)
            if template is not None:
                break
        if template is None:
            raise TemplateMissingError(name)
        blocks = find_shop_blocks(template.get_all_values())
        if not blocks:
            raise LayoutError()
        last = blocks[-1]

        new_ws = self._sheets.spreadsheet.duplicate_sheet(
            source_sheet_id=template.id, new_sheet_name=name)
        if last.title_row > 1:
            new_ws.delete_rows(1, last.title_row - 1)       # oxirgi blokdan yuqoridagi hamma narsa
        block = find_shop_blocks(new_ws.get_all_values())[0]
        self._reset_block(new_ws, block, year, month)
        block = find_shop_block(new_ws.get_all_values(), month)
        self._paint_once(new_ws, block, year, month, reset=True)
        logger.info("Do'kon: yangi yil varag'i yaratildi: %s", name)
        return new_ws

    def _append_month_block(self, ws, values, year: int, month: int):
        template = find_shop_blocks(values)[-1]
        height = template.total_row - template.title_row + 1
        new_title = template.total_row + 1 + GAP_ROWS
        new_total = new_title + height - 1
        if ws.row_count < new_total:
            ws.add_rows(new_total - ws.row_count)

        self._sheets.spreadsheet.batch_update({"requests": [{"copyPaste": {
            "source": {"sheetId": ws.id, "startRowIndex": template.title_row - 1,
                       "endRowIndex": template.total_row,
                       "startColumnIndex": 0, "endColumnIndex": ws.col_count},
            "destination": {"sheetId": ws.id, "startRowIndex": new_title - 1,
                            "endRowIndex": new_total,
                            "startColumnIndex": 0, "endColumnIndex": ws.col_count},
            "pasteType": "PASTE_NORMAL",
        }}]})

        pasted = next(b for b in find_shop_blocks(ws.get_all_values()) if b.title_row == new_title)
        self._reset_block(ws, pasted, year, month)
        values = ws.get_all_values()
        block = find_shop_block(values, month)
        self._paint_once(ws, block, year, month, reset=True)
        self._sync_colors(ws, block, year, month)
        logger.info("Do'kon: yangi oy bloki qo'shildi: %s, %s-oy", self.tab_name(year), month)
        return ws, values, block

    def _reset_block(self, ws, block: ShopBlock, year: int, month: int) -> None:
        """Nusxadagi summalarni tozalaydi, kun raqamlari va oy nomini yangilaydi.
        "Итог:" qatoriga (formula) tegmaydi."""
        first, last = block.header_row + 1, block.total_row - 1
        dim = calendar.monthrange(year, month)[1]
        ws.batch_clear([f"{cell_ref(first, block.amount_col)}:{cell_ref(last, block.amount_col)}"])
        ws.update(range_name=f"{cell_ref(first, 0)}:{cell_ref(last, 0)}",
                  values=[[d if d <= dim else ""] for d in range(1, block.capacity + 1)])
        ws.update(range_name=cell_ref(block.title_row, block.title_col),
                  values=[[MONTHS["ru"][month - 1]]])

    def _today(self) -> date:
        return datetime.now(self._tz).date() if self._tz else date.today()

    def _sync_colors(self, ws, block: ShopBlock, year: int, month: int) -> None:
        """Summa kiritilmagan kunlarni (bugungi va undan oldingi, yakshanbasiz) sariq qiladi,
        kiritilganlarini oq qaytaradi. Faqat o'zgargan kunlar yoziladi (birinchi marta hammasi)."""
        try:
            today = self._today()
            dim = calendar.monthrange(year, month)[1]
            key = (year, month)
            prev = self._marks.get(key)
            cur: dict[int, bool] = {}
            requests = []
            c1 = block.amount_col + 1
            for d, row in block.day_rows.items():
                if d > dim:
                    continue
                dt = date(year, month, d)
                if dt.weekday() == 6:                      # yakshanba: qizil, tegilmaydi
                    continue
                missing = dt <= today and not block.amounts.get(d)
                cur[d] = missing
                if prev is None or prev.get(d) != missing:
                    requests.append(_fill(ws.id, row - 1, row, 0, c1, YELLOW if missing else WHITE))
            if requests:
                self._sheets.spreadsheet.batch_update({"requests": requests})
            self._marks[key] = cur
        except Exception:
            logger.warning("Do'kon: kiritilmagan kunlarni bo'yab bo'lmadi: %s, %s-oy",
                           self.tab_name(year), month, exc_info=True)

    def _paint_once(self, ws, block: ShopBlock, year: int, month: int, reset: bool) -> None:
        """Yakshanba qatorlarini qizil qiladi (dastur ishlagan davrda har oy blokini bir marta)."""
        if reset:
            self._marks.pop((year, month), None)         # blok tozalandi: sariqlar ham yo'q
        if (year, month) in self._painted:
            return
        self._painted.add((year, month))
        try:
            row0 = block.header_row                      # birinchi kun qatori (0 dan)
            c1 = block.amount_col + 1
            dim = calendar.monthrange(year, month)[1]
            requests = []
            if reset:
                requests.append(_fill(ws.id, row0, row0 + block.capacity, 0, c1, WHITE))
            for d in range(1, dim + 1):
                if date(year, month, d).weekday() == 6:
                    requests.append(_fill(ws.id, row0 + d - 1, row0 + d, 0, c1, RED))
            if requests:
                self._sheets.spreadsheet.batch_update({"requests": requests})
        except Exception:
            logger.warning("Do'kon: yakshanbalarni bo'yab bo'lmadi: %s, %s-oy", self.tab_name(year),
                           month, exc_info=True)

    # ---------------- o'qish / yozish ----------------
    def _get_day(self, day: date) -> tuple[bool, int | None] | None:
        found = self._get_block(day.year, day.month, create=False)
        if found is None:
            return None
        block = found[2]
        if day.day not in block.day_rows:
            return None
        return bool(block.amounts.get(day.day)), block_amount(block, day.day)

    def _set_day(self, day: date, amount: int, create: bool) -> None:
        found = self._get_block(day.year, day.month, create=create)
        if found is None:
            raise NoBlockError()
        ws, _, block = found
        row = block.day_rows.get(day.day)
        if row is None:
            raise LayoutError()
        ws.update(range_name=cell_ref(row, block.amount_col), values=[[amount]])
        block.amounts[day.day] = str(amount)
        self._sync_colors(ws, block, day.year, day.month)

    def _adjust_day(self, day: date, delta: int) -> tuple[int, int]:
        found = self._get_block(day.year, day.month, create=False)
        if found is None:
            raise NoBlockError()
        ws, _, block = found
        row = block.day_rows.get(day.day)
        if row is None:
            raise LayoutError()
        old = block_amount(block, day.day) if block.amounts.get(day.day) else None
        if old is None:
            raise NotEnteredError()
        new = old + delta
        if new < 0:
            raise NegativeResultError(old)
        ws.update(range_name=cell_ref(row, block.amount_col), values=[[new]])
        block.amounts[day.day] = str(new)
        self._sync_colors(ws, block, day.year, day.month)
        return old, new

    def _read_blocks(self, months: list[tuple[int, int]]) -> dict[tuple[int, int], ShopBlock | None]:
        cache: dict[int, tuple | None] = {}
        out: dict[tuple[int, int], ShopBlock | None] = {}
        for year, month in months:
            if year not in cache:
                ws = self._find_ws(year)
                cache[year] = (ws, ws.get_all_values()) if ws is not None else None
            entry = cache[year]
            block = find_shop_block(entry[1], month) if entry is not None else None
            out[(year, month)] = block
            if block is not None:
                self._sync_colors(entry[0], block, year, month)
        return out

    def _day_statuses(self, days: list[date]) -> dict[date, str]:
        if not days:
            return {}
        blocks = self._read_blocks(months_in_range(min(days), max(days)))
        out: dict[date, str] = {}
        for d in days:
            block = blocks.get((d.year, d.month))
            if block is None or d.day not in block.day_rows:
                out[d] = "nosheet"
            else:
                out[d] = "rec" if block.amounts.get(d.day) else "none"
        return out

    def _month_total_until(self, day: date) -> int:
        found = self._get_block(day.year, day.month, create=False)
        if found is None:
            return 0
        block = found[2]
        return sum(a for d in block.day_rows if d <= day.day
                   and (a := block_amount(block, d)) is not None)
