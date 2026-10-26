"""Yo'qlama jadvalini tahlil qilish va hisob-kitob. Google Sheets ga bog'liq emas."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

PLUS = "+"        # keldi
MINUS = "-"       # sababsiz kelmadi
EXCUSED = "S"     # sababli kelmadi (jadvalga qo'lda "S" yoki "С" deb yozsangiz ham bo'ladi)

_MINUS_SIGNS = {"-", "–", "—", "−"}
_EXCUSED_SIGNS = {"s", "S", "с", "С"}  # lotin va kirill


def is_workday(d: date) -> bool:
    """Dushanba-juma: majburiy ish kuni (maosh shu kunlarga bo'linadi)."""
    return d.weekday() < 5


def is_saturday(d: date) -> bool:
    """Shanba: ixtiyoriy ish kuni. Kelmaganga ushlanma yo'q, kelgan esa kelmagan kunlarini kamaytiradi."""
    return d.weekday() == 5


def is_markable_day(d: date) -> bool:
    """Yo'qlama olish mumkin bo'lgan kunlar: dushanba-shanba. Yakshanba dam olish kuni."""
    return d.weekday() < 6


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


_MONTH_STEMS = (
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("май", 5), ("мая", 5),
    ("июн", 6), ("июл", 7), ("август", 8), ("сентябр", 9), ("сентабр", 9),
    ("октябр", 10), ("октабр", 10), ("ноябр", 11), ("декабр", 12),
    ("yanvar", 1), ("fevral", 2), ("mart", 3), ("aprel", 4), ("may", 5), ("iyun", 6),
    ("iyul", 7), ("avgust", 8), ("sentyabr", 9), ("sentabr", 9), ("oktyabr", 10),
    ("oktabr", 10), ("noyabr", 11), ("dekabr", 12),
)


def month_from_text(value: Any) -> int | None:
    """'Октябрь' / 'Oktyabr' / 'Октябрь 2026' -> 10. Oy nomi bo'lmasa None."""
    s = str(value).strip().lower().replace("ё", "е")
    if not s or len(s) > 30:
        return None
    for stem, month in _MONTH_STEMS:
        if s.startswith(stem):
            return month
    return None


def _parse_rows(values: list[list[Any]], header_idx: int, stop_idx: int) -> Grid:
    """header_idx: kun raqamlari qatori (0 dan). Ishchilar: header_idx+1 dan stop_idx gacha."""
    day_cols: dict[int, int] = {}
    for ci, v in enumerate(values[header_idx]):
        s = str(v).strip()
        if s.isdigit() and 1 <= int(s) <= 31:
            day_cols.setdefault(int(s), ci)

    rows: list[GridRow] = []
    for ri in range(header_idx + 1, stop_idx):
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


@dataclass(frozen=True, slots=True)
class Block:
    """Varaqdagi bitta oy bloki: sarlavha (oy nomi) qatori, kun raqamlari qatori, ishchilar."""
    month: int | None   # sarlavhadagi oy nomidan aniqlangan oy
    title_row: int      # 1 dan (oy nomi qatori)
    header_row: int     # 1 dan (kun raqamlari qatori)
    title_col: int      # oy nomi yozilgan ustun (0 dan)
    grid: Grid          # ishchilar (qator raqamlari varaq bo'yicha, 1 dan)
    last_row: int       # blokning oxirgi qatori (oxirgi ishchi)


def _is_day_header(row: list[Any]) -> bool:
    days = {int(str(c).strip()) for c in row
            if str(c).strip().isdigit() and 1 <= int(str(c).strip()) <= 31}
    return len(days) >= 28      # eng kami 28 kunlik oy


def find_blocks(values: list[list[Any]]) -> list[Block]:
    """Varaqdagi barcha oy bloklarini topadi. Blok kun raqamlari (1..28+) turgan qator bilan
    aniqlanadi; undan bir qator yuqorida oy nomi turadi."""
    headers = [i for i, row in enumerate(values) if _is_day_header(row)]
    blocks: list[Block] = []
    for k, h in enumerate(headers):
        if h == 0:
            continue    # oy nomi qatori yo'q
        stop = headers[k + 1] - 1 if k + 1 < len(headers) else len(values)
        title = values[h - 1]
        month, title_col = None, None
        for ci, v in enumerate(title):
            m = month_from_text(v)
            if m:
                month, title_col = m, ci
                break
        if title_col is None:
            title_col = next((ci for ci, v in enumerate(title) if str(v).strip()), 1)
        grid = _parse_rows(values, h, stop)
        last_row = max((r.row for r in grid.rows), default=h + 1)
        blocks.append(Block(month=month, title_row=h, header_row=h + 1,
                            title_col=title_col, grid=grid, last_row=last_row))
    return blocks


def find_block(values: list[list[Any]], month: int) -> Block | None:
    return next((b for b in find_blocks(values) if b.month == month), None)


def parse_grid(values: list[list[Any]]) -> Grid:
    """Varaqdagi birinchi blokning jadvali (blok bo'lmasa bo'sh)."""
    blocks = find_blocks(values)
    return blocks[0].grid if blocks else Grid(rows=[], day_cols={})


@dataclass(slots=True)
class WorkerStat:
    name: str
    came: int = 0
    absent: int = 0      # sababsiz
    excused: int = 0     # sababli
    unmarked: int = 0
    absent_days: list[date] = field(default_factory=list)   # sababsiz kelmagan sanalar
    excused_days: list[date] = field(default_factory=list)  # sababli kelmagan sanalar
    saturday_days: list[date] = field(default_factory=list)  # ishlagan shanbalar


@dataclass(slots=True)
class PeriodStats:
    workers: list[WorkerStat]
    recorded_days: int            # yo'qlama olingan ish kunlari
    work_days: int                # davrdagi jami ish kunlari (dushanba-juma)
    missing: list[tuple[int, int]]  # varag'i topilmagan (yil, oy) lar
    unrecorded: list[date]          # ish kuni bo'lsa-da yo'qlama olinmagan sanalar


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
    unrecorded: list[date] = []
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
                        st.absent_days.append(d)
                    elif mark == EXCUSED:
                        st.excused += 1
                        st.excused_days.append(d)
                    else:
                        st.unmarked += 1
            else:
                unrecorded.append(d)
        elif is_saturday(d):
            # Shanba ixtiyoriy: faqat kelganlar sanaladi (kelmagan ko'rsatilmaydi, ushlanma yo'q)
            grid = grids.get((d.year, d.month))
            if grid and d.day in grid.day_cols:
                for r in grid.rows:
                    if r.marks.get(d.day) == PLUS:
                        stats.setdefault(r.name, WorkerStat(r.name)).saturday_days.append(d)
        d += timedelta(days=1)

    return PeriodStats(workers=list(stats.values()), recorded_days=recorded,
                       work_days=work_days, missing=missing, unrecorded=unrecorded)
