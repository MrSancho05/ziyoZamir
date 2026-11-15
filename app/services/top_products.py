"""Top mahsulotlar: ombor aylanuvchanligi (velocity) tahlili. Google Sheets ga bog'liq emas.

Manba: `Приход` (kirim = to'ldirish) va `Расход` (chiqim = ishlatish) varaqlari.

Har bir mahsulot uchun:
- kirim chastotasi (necha marta to'ldirilgan) va kirim miqdori;
- chiqim chastotasi (necha marta olingan) va chiqim miqdori.

Reyting ballari (ombor "slotting" amaliyotiga mos: tez aylanadiganlar chiqishga eng yaqin turadi):
    ball = 0.40 * chiqim miqdori + 0.30 * chiqim chastotasi + 0.30 * kirim chastotasi
Har bir ko'rsatkich mahsulotlar orasidagi eng kattasiga nisbatan logarifmik shkalada 0..1 ga keltiriladi
(`log1p(x) / log1p(max)`): turli o'lchov birliklari (dona, metr, kg) va bitta-ikkita juda katta qiymat
natijani buzmaydi. Ma'lumoti yo'q ko'rsatkich (masalan, miqdor ustuni yo'q) hisobdan chiqarilib, qolgan
og'irliklar qayta taqsimlanadi.

ABC sinfi (chiqimi bor mahsulotlar ball bo'yicha tartiblanib): A — eng yuqori 20%, B — keyingi 30%, C — qolgan 50%.
D — tanlangan davrda chiqimi umuman yo'q mahsulotlar (faqat kirim bor).
"""
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.flows_grid import FlowTable, parse_cell_date, row_info

WEIGHTS = {"out_qty": 0.40, "out_cnt": 0.30, "in_cnt": 0.30}
CLASSES = ("A", "B", "C", "D")
OVERSTOCK_RATIO = 3.0     # kirim miqdori chiqimdan shuncha marta ko'p bo'lsa: ortiqcha zaxira belgisi


@dataclass(slots=True)
class ProductStat:
    name: str
    in_cnt: int = 0
    in_qty: float = 0.0
    out_cnt: int = 0
    out_qty: float = 0.0
    score: float = 0.0
    cls: str = "D"
    rank: int = 0
    no_inflow: bool = False      # tez aylanadi, lekin davrda kirim yo'q: zaxirani tekshirish kerak
    overstock: bool = False      # kirim chiqimdan ancha ko'p: ortiqcha zaxira


@dataclass(slots=True)
class TopReport:
    items: list[ProductStat] = field(default_factory=list)
    in_found: bool = True          # Приход varag'i topildimi
    out_found: bool = True         # Расход varag'i topildimi
    in_rows: int = 0
    out_rows: int = 0
    undated: list[str] = field(default_factory=list)   # sana ustuni yo'q varaqlar (davr filtri qo'llanmadi)
    weights: dict[str, float] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.items


def _key(label: str) -> str:
    return " ".join(label.split()).casefold()


def _scan(table: FlowTable, start: date | None, end: date, year: int):
    """Varaq qatorlarini (mahsulot, miqdor) ko'rinishida beradi. `start` berilsa va varaqda sana ustuni bo'lsa,
    faqat [start, end] oralig'idagi qatorlar olinadi (sanasi bo'sh qator yuqoridagi sanani oladi).
    Qaytaradi: (qatorlar, davr_filtri_qo'llandimi)."""
    date_col = table.cols.get("date")
    filtered = start is not None and date_col is not None
    rows: list[tuple[str, float | None]] = []
    current: date | None = None
    for row in table.data:
        if date_col is not None:
            raw = row[date_col] if date_col < len(row) else ""
            if raw:
                current = parse_cell_date(raw, year)
            if filtered and (current is None or not start <= current <= end):
                continue
        info = row_info(table.header, table.cols, row)
        label = info.label.strip()
        if not label:
            continue
        if info.qty is not None and info.qty <= 0:
            continue
        rows.append((label, info.qty))
    return rows, filtered


def _norm(value: float, top: float) -> float:
    return math.log1p(value) / math.log1p(top) if top > 0 else 0.0


def build_report(inflow: FlowTable | None, outflow: FlowTable | None, today: date,
                 days: int | None) -> TopReport:
    """`days` — oxirgi necha kun (None: barcha vaqt)."""
    report = TopReport(in_found=inflow is not None, out_found=outflow is not None)
    start = today - timedelta(days=days - 1) if days else None
    stats: dict[str, ProductStat] = {}

    for table, name, is_in in ((inflow, "inflow", True), (outflow, "outflow", False)):
        if table is None:
            continue
        rows, filtered = _scan(table, start, today, today.year)
        if days and not filtered:
            report.undated.append(name)
        for label, qty in rows:
            st = stats.setdefault(_key(label), ProductStat(name=" ".join(label.split())))
            if is_in:
                st.in_cnt += 1
                st.in_qty += qty or 0
                report.in_rows += 1
            else:
                st.out_cnt += 1
                st.out_qty += qty or 0
                report.out_rows += 1

    items = list(stats.values())
    if not items:
        return report

    tops = {
        "out_qty": max(s.out_qty for s in items),
        "out_cnt": max(s.out_cnt for s in items),
        "in_cnt": max(s.in_cnt for s in items),
    }
    active = {k: w for k, w in WEIGHTS.items() if tops[k] > 0}
    total_w = sum(active.values()) or 1.0
    report.weights = {k: w / total_w for k, w in active.items()}
    for s in items:
        values = {"out_qty": s.out_qty, "out_cnt": s.out_cnt, "in_cnt": s.in_cnt}
        s.score = sum(w * _norm(values[k], tops[k]) for k, w in report.weights.items())

    def order(s: ProductStat):
        return (-s.score, -s.out_qty, -s.out_cnt, -s.in_cnt, s.name.casefold())

    movers = sorted((s for s in items if s.out_cnt > 0), key=order)
    idle = sorted((s for s in items if s.out_cnt == 0), key=order)

    n = len(movers)
    a_cut = max(1, math.ceil(n * 0.2)) if n else 0
    b_cut = max(a_cut, math.ceil(n * 0.5))
    for i, s in enumerate(movers):
        s.cls = "A" if i < a_cut else "B" if i < b_cut else "C"
        s.no_inflow = report.in_found and s.cls == "A" and s.in_cnt == 0
        s.overstock = (tops["out_qty"] > 0 and s.cls != "A" and s.in_qty > 0
                       and s.in_qty >= OVERSTOCK_RATIO * s.out_qty)
    for s in idle:
        s.cls = "D"
        s.overstock = s.in_qty > 0

    report.items = movers + idle
    for i, s in enumerate(report.items, 1):
        s.rank = i
    return report
