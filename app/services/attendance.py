import calendar
import logging
from datetime import date

from gspread.exceptions import WorksheetNotFound

from app.locales.texts import MONTHS
from app.services.attendance_grid import (
    EXCUSED,
    MINUS,
    PLUS,
    Grid,
    PeriodStats,
    cell_ref,
    compute_stats,
    months_in_range,
    parse_grid,
)
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)


class TemplateMissingError(Exception):
    """Kerakli oy varag'i ham yo'q, nusxa olinadigan oldingi oy varag'i ham yo'q."""

    def __init__(self, tab: str) -> None:
        super().__init__(tab)
        self.tab = tab


class LayoutError(Exception):
    """Jadval tuzilishi kutilganga mos emas (2-qatorda kun raqamlari yo'q)."""


class SheetChangedError(Exception):
    """Yo'qlama olish paytida jadvaldagi ishchilar ro'yxati o'zgarib qolgan."""


class AttendanceService:
    """Oylik yo'qlama varaqlarini o'qiydi/yozadi. Har oy uchun alohida varaq:
    nomi ATTENDANCE_TAB_FORMAT bo'yicha (masalan 'Davomat 2026-10')."""

    def __init__(self, sheets: SheetsClient, tab_format: str) -> None:
        self._sheets = sheets
        self._tab_format = tab_format

    # ---------------- ommaviy (async) ----------------
    async def load_day(self, day: date) -> tuple[list[str], list[str | None]]:
        return await self._sheets.run(self._load_day, day)

    async def save_day(self, day: date, names: list[str], marks: list[str]) -> None:
        await self._sheets.run(self._save_day, day, names, marks)

    async def is_day_recorded(self, day: date) -> bool:
        return await self._sheets.run(self._is_day_recorded, day)

    async def period_stats(self, start: date, end: date) -> PeriodStats:
        grids = await self._sheets.run(self._read_grids, months_in_range(start, end))
        return compute_stats(grids, start, end)

    # ---------------- ichki (sinxron) ----------------
    def tab_name(self, year: int, month: int) -> str:
        return self._tab_format.format(year=year, month=month)

    def _find(self, year: int, month: int):
        try:
            return self._sheets.spreadsheet.worksheet(self.tab_name(year, month))
        except WorksheetNotFound:
            return None

    def _ensure_ws(self, year: int, month: int):
        ws = self._find(year, month)
        if ws is not None:
            return ws

        # Yangi oy: eng yaqin oldingi oy varag'idan nusxa olinadi
        template, y, m = None, year, month
        for _ in range(24):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            template = self._find(y, m)
            if template is not None:
                break
        if template is None:
            raise TemplateMissingError(self.tab_name(year, month))

        new_ws = self._sheets.spreadsheet.duplicate_sheet(
            source_sheet_id=template.id, new_sheet_name=self.tab_name(year, month)
        )
        self._reset_month(new_ws, year, month)
        logger.info("Yangi oy varag'i yaratildi: %s", self.tab_name(year, month))
        return new_ws

    def _reset_month(self, ws, year: int, month: int) -> None:
        """Nusxadan belgilarni tozalaydi, kun raqamlari va oy nomini yangilaydi."""
        values = ws.get_all_values()
        grid = parse_grid(values)
        if not grid.day_cols:
            raise LayoutError()

        cols = sorted(grid.day_cols.values())
        c_min, c_max = cols[0], cols[-1]
        last_row = max(len(values), 3)
        ws.batch_clear([f"{cell_ref(3, c_min)}:{cell_ref(last_row, c_max)}"])

        col_to_day = {c: d for d, c in grid.day_cols.items()}
        days_in_month = calendar.monthrange(year, month)[1]
        header = [
            col_to_day[c] if c in col_to_day and col_to_day[c] <= days_in_month else ""
            for c in range(c_min, c_max + 1)
        ]
        ws.update(range_name=f"{cell_ref(2, c_min)}:{cell_ref(2, c_max)}", values=[header])

        first_row = values[0] if values else []
        title_col = next((i for i, v in enumerate(first_row) if str(v).strip()), 1)
        ws.update(range_name=cell_ref(1, title_col), values=[[MONTHS["ru"][month - 1]]])

    def _load_day(self, day: date) -> tuple[list[str], list[str | None]]:
        ws = self._ensure_ws(day.year, day.month)
        grid = parse_grid(ws.get_all_values())
        if day.day not in grid.day_cols:
            raise LayoutError()
        return [r.name for r in grid.rows], [r.marks.get(day.day) for r in grid.rows]

    def _is_day_recorded(self, day: date) -> bool:
        ws = self._find(day.year, day.month)   # yangi varaq yaratmaydi
        if ws is None:
            return False
        grid = parse_grid(ws.get_all_values())
        return day.day in grid.day_cols and any(day.day in r.marks for r in grid.rows)

    def _save_day(self, day: date, names: list[str], marks: list[str]) -> None:
        ws = self._ensure_ws(day.year, day.month)
        values = ws.get_all_values()
        grid = parse_grid(values)
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
            if mark not in (PLUS, MINUS, EXCUSED):
                raise ValueError(f"Noto'g'ri belgi: {mark!r}")
            column[r.row - r_min][0] = mark

        ws.update(range_name=f"{cell_ref(r_min, col)}:{cell_ref(r_max, col)}", values=column)

    def _read_grids(self, months: list[tuple[int, int]]) -> dict[tuple[int, int], Grid | None]:
        grids: dict[tuple[int, int], Grid | None] = {}
        for year, month in months:
            ws = self._find(year, month)
            grids[(year, month)] = parse_grid(ws.get_all_values()) if ws is not None else None
        return grids
