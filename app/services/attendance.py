import calendar
import logging
from datetime import date

from gspread.exceptions import WorksheetNotFound

from app.locales.texts import MONTHS
from app.services.attendance_grid import (
    EXCUSED,
    MINUS,
    PLUS,
    Block,
    Grid,
    PeriodStats,
    cell_ref,
    compute_stats,
    find_block,
    find_blocks,
    months_in_range,
)
from app.services.payroll import (
    MonthReport,
    compute_pay,
    find_money_cols,
    parse_money,
    working_days_in_month,
)
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)

RED = {"red": 0.9, "green": 0.45, "blue": 0.45}      # yakshanba (dam olish kuni)
SAT_COLOR = {"red": 1.0, "green": 0.9, "blue": 0.6}   # shanba (ixtiyoriy ish kuni)
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
GAP_ROWS = 2                                          # oy bloklari orasidagi bo'sh qatorlar


def _fill(sheet_id: int, row0: int, row1: int, col0: int, col1: int, color: dict) -> dict:
    """Google Sheets API: [row0, row1) x [col0, col1) kataklarni bo'yash (0 dan sanaladi)."""
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": row0, "endRowIndex": row1,
                  "startColumnIndex": col0, "endColumnIndex": col1},
        "cell": {"userEnteredFormat": {"backgroundColor": color}},
        "fields": "userEnteredFormat.backgroundColor",
    }}


class TemplateMissingError(Exception):
    """Yillik varaq ham yo'q, nusxa olinadigan oldingi yil varag'i ham yo'q."""

    def __init__(self, tab: str) -> None:
        super().__init__(tab)
        self.tab = tab


class LayoutError(Exception):
    """Jadval tuzilishi kutilganga mos emas (kun raqamlari qatori / oy bloki topilmadi)."""


class SheetChangedError(Exception):
    """Yo'qlama olish paytida jadvaldagi ishchilar ro'yxati o'zgarib qolgan."""


class AttendanceService:
    """Yo'qlama jadvali: har YIL uchun bitta varaq (masalan 'Davomat 2026'), ichida oylar
    bloklar bo'lib ketma-ket joylashadi. Blok oy nomi (sarlavha qatori) bo'yicha topiladi.

    Yangi oy: oxirgi blokning nusxasi pastga qo'yiladi. Yangi yil: oldingi yil varag'ining
    oxirgi bloki bilan yangi varaq ochiladi."""

    def __init__(self, sheets: SheetsClient, tab_format: str, deduct_excused: bool = False) -> None:
        self._sheets = sheets
        self._tab_format = tab_format
        self._deduct_excused = deduct_excused
        self._painted: set[tuple[int, int]] = set()

    # ---------------- ommaviy (async) ----------------
    async def load_day(self, day: date) -> tuple[list[str], list[str | None]]:
        return await self._sheets.run(self._load_day, day)

    async def save_day(self, day: date, names: list[str], marks: list[str | None]) -> None:
        await self._sheets.run(self._save_day, day, names, marks)

    async def is_day_recorded(self, day: date) -> bool:
        return await self._sheets.run(self._is_day_recorded, day)

    async def peek_day(self, day: date) -> tuple[list[str], list[str | None]] | None:
        """Faqat o'qiydi, hech narsa yaratmaydi. Oy bloki yoki kun ustuni yo'q bo'lsa None."""
        return await self._sheets.run(self._peek_day, day)

    async def day_statuses(self, days: list[date]) -> dict[date, str]:
        """Har bir kun uchun: 'rec' (yo'qlama olingan), 'none' (olinmagan), 'nosheet' (jadvalda oy bloki yo'q)."""
        return await self._sheets.run(self._day_statuses, days)

    async def month_payroll(self, year: int, month: int, until: date) -> MonthReport:
        """Oy davomati + maosh hisobi (`until` sanagacha). Hech narsa yaratmaydi."""
        return await self._sheets.run(self._month_payroll, year, month, until)

    async def period_stats(self, start: date, end: date) -> PeriodStats:
        grids = await self._sheets.run(self._read_grids, months_in_range(start, end))
        return compute_stats(grids, start, end)

    # ---------------- varaq va bloklar (sinxron) ----------------
    def tab_name(self, year: int) -> str:
        return self._tab_format.format(year=year, month=1)

    def _find_ws(self, year: int):
        try:
            return self._sheets.spreadsheet.worksheet(self.tab_name(year))
        except WorksheetNotFound:
            return None

    def _get_block(self, year: int, month: int, create: bool):
        """(varaq, qiymatlar, blok) yoki None. create=True bo'lsa yo'q varaq/blok yaratiladi."""
        ws = self._find_ws(year)
        if ws is None:
            if not create:
                return None
            ws = self._create_year_ws(year, month)

        values = ws.get_all_values()
        block = find_block(values, month)
        if block is None:
            if not create:
                return None
            if not find_blocks(values):
                raise LayoutError()
            return self._append_month_block(ws, values, year, month)

        self._paint_once(ws, block, year, month, reset=False)
        return ws, values, block

    def _create_year_ws(self, year: int, month: int):
        """Yangi yil: oldingi yil varag'ining oxirgi oy bloki qoladi, qolgani o'chiriladi."""
        name = self.tab_name(year)
        template = None
        for back in range(1, 6):
            template = self._find_ws(year - back)
            if template is not None:
                break
        if template is None:
            raise TemplateMissingError(name)

        blocks = find_blocks(template.get_all_values())
        if not blocks:
            raise LayoutError()
        last = blocks[-1]

        new_ws = self._sheets.spreadsheet.duplicate_sheet(
            source_sheet_id=template.id, new_sheet_name=name
        )
        if last.title_row > 1:
            new_ws.delete_rows(1, last.title_row - 1)   # oxirgi blokdan yuqoridagi hamma narsa

        values = new_ws.get_all_values()
        block = find_blocks(values)[0]
        span = self._reset_block(new_ws, block, year, month)
        block = find_block(new_ws.get_all_values(), month)
        self._paint_once(new_ws, block, year, month, reset=True, span=span)
        self._safe_write_payable(new_ws, year, month)
        logger.info("Yangi yil varag'i yaratildi: %s", name)
        return new_ws

    def _append_month_block(self, ws, values, year: int, month: int):
        """Oxirgi blokning nusxasini (format, ishchilar, oylik ustuni bilan) pastga qo'yadi."""
        template = find_blocks(values)[-1]
        height = template.last_row - template.title_row + 1
        new_title = template.last_row + 1 + GAP_ROWS
        new_last = new_title + height - 1
        if ws.row_count < new_last:
            ws.add_rows(new_last - ws.row_count)

        self._sheets.spreadsheet.batch_update({"requests": [{"copyPaste": {
            "source": {"sheetId": ws.id, "startRowIndex": template.title_row - 1,
                       "endRowIndex": template.last_row,
                       "startColumnIndex": 0, "endColumnIndex": ws.col_count},
            "destination": {"sheetId": ws.id, "startRowIndex": new_title - 1,
                            "endRowIndex": new_last,
                            "startColumnIndex": 0, "endColumnIndex": ws.col_count},
            "pasteType": "PASTE_NORMAL",
        }}]})

        pasted = next(b for b in find_blocks(ws.get_all_values()) if b.title_row == new_title)
        span = self._reset_block(ws, pasted, year, month)
        values = ws.get_all_values()
        block = find_block(values, month)
        self._paint_once(ws, block, year, month, reset=True, span=span)
        self._safe_write_payable(ws, year, month)
        logger.info("Yangi oy bloki qo'shildi: %s, %s-oy", self.tab_name(year), month)
        return ws, ws.get_all_values(), find_block(ws.get_all_values(), month)

    def _reset_block(self, ws, block: Block, year: int, month: int) -> tuple[int, int]:
        """Nusxadagi belgilarni tozalaydi, kun raqamlari va oy nomini yangilaydi.
        Qaytaradi: kun ustunlari oralig'i (c_min, c_max), 31 tagacha."""
        if not block.grid.day_cols:
            raise LayoutError()
        # Kun ustunlari ketma-ket: 1-kun ustunidan 31 tasi. Nusxa 30 kunlik oydan kelgan bo'lsa,
        # undagi 31-ustun bo'sh bo'ladi, shuning uchun oralig'ni nusxaga qarab emas, 1-kundan hisoblaymiz.
        c_min = block.grid.day_cols.get(1, min(block.grid.day_cols.values()))
        c_max = c_min + 30

        if block.grid.rows:
            ws.batch_clear([f"{cell_ref(block.header_row + 1, c_min)}:{cell_ref(block.last_row, c_max)}"])

        days_in_month = calendar.monthrange(year, month)[1]
        header = [d if d <= days_in_month else "" for d in range(1, 32)]
        ws.update(range_name=f"{cell_ref(block.header_row, c_min)}:{cell_ref(block.header_row, c_max)}",
                  values=[header])
        ws.update(range_name=cell_ref(block.title_row, block.title_col),
                  values=[[MONTHS["ru"][month - 1]]])
        return c_min, c_max

    # ---------------- kun bo'yicha o'qish / yozish ----------------
    def _load_day(self, day: date) -> tuple[list[str], list[str | None]]:
        _, _, block = self._get_block(day.year, day.month, create=True)
        if day.day not in block.grid.day_cols:
            raise LayoutError()
        return [r.name for r in block.grid.rows], [r.marks.get(day.day) for r in block.grid.rows]

    def _save_day(self, day: date, names: list[str], marks: list[str]) -> None:
        ws, values, block = self._get_block(day.year, day.month, create=True)
        grid = block.grid
        if day.day not in grid.day_cols:
            raise LayoutError()
        if [r.name for r in grid.rows] != names:
            raise SheetChangedError()

        col = grid.day_cols[day.day]
        r_min = min(r.row for r in grid.rows)
        r_max = max(r.row for r in grid.rows)

        # Ishchi bo'lmagan oraliq qatorlar o'z holicha qoladi
        column = []
        for row_no in range(r_min, r_max + 1):
            line = values[row_no - 1] if row_no - 1 < len(values) else []
            column.append([line[col] if col < len(line) else ""])
        for r, mark in zip(grid.rows, marks):
            if mark is None and day.weekday() == 5:
                column[r.row - r_min][0] = ""          # shanba: kelmaganlar bo'sh qoladi
                continue
            if mark not in (PLUS, MINUS, EXCUSED):
                raise ValueError(f"Noto'g'ri belgi: {mark!r}")
            column[r.row - r_min][0] = mark

        ws.update(range_name=f"{cell_ref(r_min, col)}:{cell_ref(r_max, col)}", values=column)
        self._safe_write_payable(ws, day.year, day.month)

    def _peek_day(self, day: date) -> tuple[list[str], list[str | None]] | None:
        found = self._get_block(day.year, day.month, create=False)
        if found is None:
            return None
        grid = found[2].grid
        if day.day not in grid.day_cols:
            return None
        return [r.name for r in grid.rows], [r.marks.get(day.day) for r in grid.rows]

    def _day_statuses(self, days: list[date]) -> dict[date, str]:
        if not days:
            return {}
        grids = self._read_grids(months_in_range(min(days), max(days)))
        out: dict[date, str] = {}
        for d in days:
            grid = grids.get((d.year, d.month))
            if grid is None or d.day not in grid.day_cols:
                out[d] = "nosheet"
            elif any(d.day in r.marks for r in grid.rows):
                out[d] = "rec"
            else:
                out[d] = "none"
        return out

    def _is_day_recorded(self, day: date) -> bool:
        found = self._get_block(day.year, day.month, create=False)
        if found is None:
            return False
        grid = found[2].grid
        return day.day in grid.day_cols and any(day.day in r.marks for r in grid.rows)

    def _read_grids(self, months: list[tuple[int, int]]) -> dict[tuple[int, int], Grid | None]:
        cache: dict[int, list[list[str]] | None] = {}
        grids: dict[tuple[int, int], Grid | None] = {}
        for year, month in months:
            if year not in cache:
                ws = self._find_ws(year)
                cache[year] = ws.get_all_values() if ws is not None else None
            values = cache[year]
            block = find_block(values, month) if values is not None else None
            grids[(year, month)] = block.grid if block is not None else None
        return grids

    # ---------------- bo'yash ----------------
    def _paint_once(self, ws, block: Block, year: int, month: int, reset: bool,
                    span: tuple[int, int] | None = None) -> None:
        """Dastur ishlagan davrda har bir oy blokini bir marta bo'yaydi.
        Bo'yash xatosi yo'qlama olishga to'sqinlik qilmaydi."""
        if (year, month) in self._painted:
            return
        self._painted.add((year, month))
        try:
            self._paint_weekends(ws, block, year, month, reset, span)
        except Exception:
            logger.warning("Dam olish kunlarini bo'yab bo'lmadi: %s, %s-oy", self.tab_name(year),
                           month, exc_info=True)

    def _paint_weekends(self, ws, block: Block, year: int, month: int, reset: bool,
                        span: tuple[int, int] | None = None) -> None:
        """Yakshanba ustunlarini qizil, shanba ustunlarini och sariq (ixtiyoriy ish kuni) qiladi
        (kun raqamidan oxirgi ishchigacha).
        reset=True (yangi nusxa): avval hamma kun ustunlari oqlanadi, chunki nusxa oldingi
        oyning qizil ustunlari bilan kelgan. `span` - nusxadagi barcha kun ustunlari (31 tagacha)."""
        grid = block.grid
        if not grid.day_cols or not grid.rows:
            return
        row0, row1 = block.header_row - 1, block.last_row     # [row0, row1) - 0 dan
        days_in_month = calendar.monthrange(year, month)[1]
        cols = list(grid.day_cols.values())

        requests = []
        if reset:
            c0, c1 = span if span else (min(cols), max(cols))
            requests.append(_fill(ws.id, row0, row1, c0, c1 + 1, WHITE))
        for day, col in grid.day_cols.items():
            if day > days_in_month:
                continue
            weekday = date(year, month, day).weekday()
            if weekday == 6:
                requests.append(_fill(ws.id, row0, row1, col, col + 1, RED))
            elif weekday == 5:
                requests.append(_fill(ws.id, row0, row1, col, col + 1, SAT_COLOR))
        if requests:
            self._sheets.spreadsheet.batch_update({"requests": requests})

    # ---------------- maosh ----------------
    @staticmethod
    def _salaries(values: list[list[str]], grid: Grid, salary_col: int) -> dict[int, int | None]:
        out: dict[int, int | None] = {}
        for r in grid.rows:
            line = values[r.row - 1] if r.row - 1 < len(values) else []
            out[r.row] = parse_money(line[salary_col]) if salary_col < len(line) else None
        return out

    @staticmethod
    def _money_cols(values: list[list[str]], block: Block) -> tuple[int | None, int | None]:
        return find_money_cols(values, block.grid.day_cols,
                               rows=(block.title_row - 1, block.header_row - 1))

    def _safe_write_payable(self, ws, year: int, month: int) -> None:
        """Maosh ustunini yangilash xatosi yo'qlamani saqlashga to'sqinlik qilmasin."""
        try:
            self._write_payable(ws, year, month)
        except Exception:
            logger.warning("To'lanadigan maoshni yangilab bo'lmadi: %s, %s-oy", self.tab_name(year),
                           month, exc_info=True)

    def _write_payable(self, ws, year: int, month: int) -> None:
        """Shu oy blokidagi 'Oy uchun berilishi kerak bo'lgan maosh' ustunini qayta hisoblaydi."""
        values = ws.get_all_values()
        block = find_block(values, month)
        if block is None or not block.grid.rows:
            return
        salary_col, payable_col = self._money_cols(values, block)
        if salary_col is None or payable_col is None:
            return
        grid = block.grid
        pays = compute_pay(grid.rows, self._salaries(values, grid, salary_col),
                           year, month, self._deduct_excused)

        r_min = min(r.row for r in grid.rows)
        r_max = max(r.row for r in grid.rows)
        column = []
        for row_no in range(r_min, r_max + 1):       # ishchi bo'lmagan qatorlar o'z holicha qoladi
            line = values[row_no - 1] if row_no - 1 < len(values) else []
            column.append([line[payable_col] if payable_col < len(line) else ""])
        for r, pay in zip(grid.rows, pays):
            if pay.payable is not None:
                column[r.row - r_min][0] = pay.payable
        ws.update(
            range_name=f"{cell_ref(r_min, payable_col)}:{cell_ref(r_max, payable_col)}", values=column
        )

    def _month_payroll(self, year: int, month: int, until: date) -> MonthReport:
        first = date(year, month, 1)
        end = min(date(year, month, calendar.monthrange(year, month)[1]), until)
        work_days = working_days_in_month(year, month)

        ws = self._find_ws(year)
        values = ws.get_all_values() if ws is not None else []
        block = find_block(values, month)
        if block is None:
            stats = compute_stats({(year, month): None}, first, end)
            return MonthReport(stats, None, work_days, self._deduct_excused, tab_found=False)

        stats = compute_stats({(year, month): block.grid}, first, end)
        salary_col, _ = self._money_cols(values, block)
        pays = None
        if salary_col is not None:
            pays = compute_pay(block.grid.rows, self._salaries(values, block.grid, salary_col),
                               year, month, self._deduct_excused)
        return MonthReport(stats, pays, work_days, self._deduct_excused)
