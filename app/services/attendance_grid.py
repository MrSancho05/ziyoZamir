"""Yo'qlama jadvalini tahlil qilish va hisob-kitob. Google Sheets ga bog'liq emas."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

PLUS = "+"        # keldi
MINUS = "-"       # sababsiz kelmadi
EXCUSED = "S"     # sababli kelmadi (jadvalga qo'lda "S" yoki "С" deb yozsangiz ham bo'ladi)

_MINUS_SIGNS = {"-", "–", "—", "−"}
_EXCUSED_SIGNS = {"s", "S", "с", "С"}  # lotin va kirill


def is_workday(d: date) -> bool:
    """Dushanba-juma ish kuni. Shanba va yakshanba dam olish kuni."""
    return d.weekday() < 5


def parse_mark(value: Any) -> str | None:
    v = str(value).strip()
    if v == PLUS:
        return PLUS
    if v in _MINUS_SIGNS:
        return MINUS
    if v in _EXCUSED_SIGNS:
        return EXCUSED
    return None


def cell_ref(row: int, col0: int) -> str:
    """(1-asosli qator, 0-asosli ustun) -> 'C5' kabi manzil."""
    n, letters = col0 + 1, ""
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return f"{letters}{row}"


@dataclass(frozen=True, slots=True)
class GridRow:
    row: int                 # jadvaldagi qator raqami (1 dan)
    name: str
    marks: dict[int, str]    # kun -> "+", "-" yoki "S"


@dataclass(frozen=True, slots=True)
class Grid:
    rows: list[GridRow]
    day_cols: dict[int, int]  # kun -> ustun indeksi (0 dan)


def parse_grid(values: list[list[Any]]) -> Grid:
    """1-qator: oy nomi. 2-qator: 'Исм' va kun raqamlari. 3-qatordan: ishchilar."""
    header = values[1] if len(values) > 1 else []
    day_cols: dict[int, int] = {}
    for ci, v in enumerate(header):
        s = str(v).strip()
        if s.isdigit() and 1 <= int(s) <= 31:
            day_cols.setdefault(int(s), ci)

    rows: list[GridRow] = []
    for ri in range(2, len(values)):
        line = values[ri]
        name = str(line[0]).strip() if line else ""
        if not name:
            continue
        marks: dict[int, str] = {}
        for day, ci in day_cols.items():
            if ci < len(line):
                m = parse_mark(line[ci])
                if m:
                    marks[day] = m
        rows.append(GridRow(row=ri + 1, name=name, marks=marks))
    return Grid(rows=rows, day_cols=day_cols)


@dataclass(slots=True)
class WorkerStat:
    name: str
    came: int = 0
    absent: int = 0      # sababsiz
    excused: int = 0     # sababli
    unmarked: int = 0


@dataclass(slots=True)
class PeriodStats:
    workers: list[WorkerStat]
    recorded_days: int            # yo'qlama olingan ish kunlari
    work_days: int                # davrdagi jami ish kunlari (dushanba-juma)
    missing: list[tuple[int, int]]  # varag'i topilmagan (yil, oy) lar


def months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    d = start.replace(day=1)
    while d <= end:
        result.append((d.year, d.month))
        d = (d + timedelta(days=32)).replace(day=1)
    return result


def compute_stats(grids: dict[tuple[int, int], Grid | None], start: date, end: date) -> PeriodStats:
    """Davr uchun har bir ishchining kelgan / sababli / sababsiz kelmagan kunlari.

    Faqat ish kunlari (dushanba-juma) hisobga olinadi. "Yo'qlama olingan kun" =
    shu kuni kamida bitta ishchiga belgi qo'yilgan kun.
    """
    stats: dict[str, WorkerStat] = {}
    recorded = work_days = 0
    missing = [ym for ym in months_in_range(start, end) if grids.get(ym) is None]

    d = start
    while d <= end:
        if is_workday(d):
            work_days += 1
            grid = grids.get((d.year, d.month))
            if grid and d.day in grid.day_cols and any(d.day in r.marks for r in grid.rows):
                recorded += 1
                for r in grid.rows:
                    st = stats.setdefault(r.name, WorkerStat(r.name))
                    mark = r.marks.get(d.day)
                    if mark == PLUS:
                        st.came += 1
                    elif mark == MINUS:
                        st.absent += 1
                    elif mark == EXCUSED:
                        st.excused += 1
                    else:
                        st.unmarked += 1
        d += timedelta(days=1)

    return PeriodStats(workers=list(stats.values()), recorded_days=recorded,
                       work_days=work_days, missing=missing)
