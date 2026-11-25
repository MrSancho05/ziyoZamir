import logging
from dataclasses import dataclass, field
from datetime import date

from gspread.exceptions import WorksheetNotFound

from app.services.flows_grid import (
    FlowRow,
    FlowTable,
    analyze,
    diff_new,
    has_detail_cols,
    is_complete_row,
    norm_rows,
    parse_cell_date,
    row_info,
    rows_for_day,
    sheet_hash,
)
from app.services.sheets import SheetsClient
from app.storage.flows_store import FlowStore

logger = logging.getLogger(__name__)

KEYS = ("inflow", "outflow")     # Приход, Расход


@dataclass(slots=True)
class FlowDelta:
    """Bitta varaqning (Приход yoki Расход) kun boshiga nisbatan o'zgarishi."""
    key: str
    sheet_found: bool = True
    baseline: bool = True                        # solishtirish uchun avvalgi nusxa bormi
    changed: bool = False
    new_rows: list[FlowRow] = field(default_factory=list)
    header: tuple[str, ...] = ()
    cols_found: bool = False
    rows: list[tuple[str, ...]] = field(default_factory=list)
    digest: str = ""


@dataclass(slots=True)
class FlowSummary:
    count: int = 0
    amount: int = 0
    top: list[tuple[str, float, int]] = field(default_factory=list)   # (mahsulot, miqdor, summa)


class FlowsService:
    """Приход / Расход varaqlari. Varaq tuzilishi oldindan noma'lum bo'lgani uchun ish nusxa
    (snapshot) bo'yicha: kun yopilganda varaqning holati saqlanadi, ertasi kuni undan farq "bugungi
    kirim/chiqim" bo'ladi."""

    def __init__(self, sheets: SheetsClient, store: FlowStore, tabs: dict[str, str]) -> None:
        self._sheets = sheets
        self._store = store
        self._tabs = tabs

    @property
    def tabs(self) -> dict[str, str]:
        return self._tabs

    async def tables(self) -> dict[str, FlowTable | None]:
        """Приход / Расход varaqlarining to'liq tarixi (tahlil uchun). Varaq topilmasa — None."""
        out: dict[str, FlowTable | None] = {}
        for key in KEYS:
            values = await self._sheets.run(self._read, key)
            out[key] = None if values is None else analyze(norm_rows(values))
        return out

    def _read(self, key: str) -> list[list[str]] | None:
        try:
            return self._sheets.spreadsheet.worksheet(self._tabs[key]).get_all_values()
        except WorksheetNotFound:
            return None

    async def _delta(self, key: str, day: date) -> FlowDelta:
        """`day` kunining to'liq qatorlari. Varaqda sana ustuni bo'lsa, faqat shu sanali qatorlar olinadi
        (o'tgan sanalardagi o'zgarishlar e'tiborga olinmaydi); bo'lmasa kun boshidagi nusxadan farq olinadi.
        Ikkala holatda ham faqat sanasi bor, mahsulot/miqdor/summasi yo'q qatorlar tashlab yuboriladi."""
        values = await self._sheets.run(self._read, key)
        if values is None:
            return FlowDelta(key=key, sheet_found=False)
        rows = norm_rows(values)
        digest = sheet_hash(rows)
        table = analyze(rows)
        cols_found = has_detail_cols(table.cols)

        if table.cols.get("date") is not None:
            new_rows = [row_info(table.header, table.cols, r) for r in rows_for_day(table, day)]
            return FlowDelta(key=key, baseline=True, changed=bool(new_rows), new_rows=new_rows,
                             header=table.header, cols_found=cols_found, rows=rows, digest=digest)

        snap = await self._store.get_snapshot(key)
        if snap is None:
            return FlowDelta(key=key, baseline=False, header=table.header, cols_found=cols_found,
                             rows=rows, digest=digest)
        old_data = analyze(snap.rows).data
        new_rows = [row_info(table.header, table.cols, r)
                    for r in diff_new(old_data, table.data)
                    if is_complete_row(table.header, table.cols, r)]
        return FlowDelta(key=key, changed=bool(new_rows), new_rows=new_rows, header=table.header,
                         cols_found=cols_found, rows=rows, digest=digest)

    async def preview(self, day: date) -> dict[str, FlowDelta]:
        """Hech narsani saqlamaydi: `day` (bugun) kunining kirim/chiqimi."""
        return {key: await self._delta(key, day) for key in KEYS}

    async def commit(self, day: date) -> dict[str, FlowDelta]:
        """Kunni yopadi: yangi qatorlar tarixga yoziladi, nusxa yangilanadi."""
        deltas = await self.preview(day)
        for key, d in deltas.items():
            if not d.sheet_found:
                continue
            if d.baseline:
                # qayta ishga tushsa ham takror yozilmasligi uchun shu kunning eski yozuvlari almashtiriladi
                await self._store.replace_rows(key, day.isoformat(),
                                               [(r.label, r.qty, r.amount) for r in d.new_rows])
            await self._store.save_snapshot(key, day.isoformat(), d.digest, d.rows)
        return deltas

    async def rebuild_history(self, before: date) -> dict[str, int]:
        """Saqlangan kunlik yozuvlarni (flow_rows) varaqning o'zidan qayta quradi: `before` dan oldingi har bir
        sana uchun shu sanadagi to'liq qatorlar. Eski (noto'g'ri o'qilgan) summalar va keyin qo'shilgan tarix
        (masalan, import) shu bilan tuzatiladi. Sana ustuni bo'lmagan varaqqa tegilmaydi."""
        counts: dict[str, int] = {}
        for key in KEYS:
            values = await self._sheets.run(self._read, key)
            if values is None:
                continue
            table = analyze(norm_rows(values))
            col = table.cols.get("date")
            if col is None:
                continue
            by_day: dict[date, list[FlowRow]] = {}
            current: date | None = None
            for row in table.data:
                raw = row[col] if col < len(row) else ""
                if raw:
                    current = parse_cell_date(raw, before.year)
                if current is None or current >= before or not is_complete_row(table.header, table.cols, row):
                    continue
                by_day.setdefault(current, []).append(row_info(table.header, table.cols, row))
            for day, rows in sorted(by_day.items()):
                await self._store.replace_rows(key, day.isoformat(), [(r.label, r.qty, r.amount) for r in rows])
            counts[key] = sum(len(v) for v in by_day.values())
        return counts

    async def rows(self, key: str, start: date, end: date):
        """Kunlik yozuvlar (flows_store): Jonli Dashboard grafiklari uchun. Google Sheets o'qilmaydi."""
        return await self._store.rows_between(key, start.isoformat(), end.isoformat())

    async def summary(self, start: date, end: date) -> dict[str, FlowSummary]:
        out: dict[str, FlowSummary] = {}
        for key in KEYS:
            rows = await self._store.rows_between(key, start.isoformat(), end.isoformat())
            by_label: dict[str, list[float]] = {}
            amount = 0
            for r in rows:
                amount += r.amount or 0
                agg = by_label.setdefault(r.label.strip().casefold() or "—", [0.0, 0.0, r.label.strip() or "—"])
                agg[0] += r.qty or 0
                agg[1] += r.amount or 0
            top = sorted(((v[2], v[0], int(v[1])) for v in by_label.values()), key=lambda x: (-x[2], -x[1]))
            out[key] = FlowSummary(count=len(rows), amount=amount, top=top[:5])
        return out
