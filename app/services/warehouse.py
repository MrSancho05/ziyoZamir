"""Sklad: Приход / Расход / Остатки и Расходы / Справочник varaqlari bilan ishlash.

Ustunlar sarlavha nomi bo'yicha topiladi (tartibi o'zgarsa ham ishlaydi):
- Приход:  Дата | Наименование товара | Ед. изм. | Количество | Цена за ед. (сум) | Общая сумма (сум) | Поставщик / Примечание
- Расход:  Дата | Наименование товара | Количество расхода | Заказ / Цех / На какое изделие | Ответственный |
           Сумма расхода (сум)  <- varaq formulasi, bot HECH QACHON yozmaydi
- Остатки и Расходы: Наименование товара | Категория | Ед. изм. | В упаковке (шт) | ... formulalar ... | Мин. остаток | Статус
- Справочник: "Поставщики" va "Ответственные" ustunlari (ro'yxat ko'rinishida)

Barcha sinxron funksiyalar SheetsClient.run() orqali, bitta qulf ostida bajariladi: o'qish + yozish
bir-biriga aralashib ketmaydi.

Yangi qator qo'shilganda (Table1/Table2/Table3 ichida qolishi uchun):
- sana ISO (YYYY-MM-DD) ko'rinishida USER_ENTERED bilan yoziladi — varaq uni 2-qator kabi formatlaydi;
- 2-qatorning formati va ochiladigan ro'yxatlari (PASTE_FORMAT, PASTE_DATA_VALIDATION) va formulalari ko'chiriladi;
- Google Sheets jadvali (Table) chegarasi `updateTable` orqali yangi qatorgacha kengaytiriladi.
Ustunlar hech qachon o'rtaga qo'shilmaydi: yetishmagan ustun jadval OXIRIGA qo'shiladi (migrate_schema).
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gspread.exceptions import WorksheetNotFound
from gspread.utils import ValueInputOption, ValueRenderOption, rowcol_to_a1

from app.services.categories import merge as merge_categories
from app.services.flows_grid import parse_cell_date
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)

CACHE_TTL = 60.0          # soniya
SERIAL_EPOCH = date(1899, 12, 30)       # Google Sheets sana seriya raqamining boshlanishi
YELLOW = {"red": 1.0, "green": 0.95, "blue": 0.6}      # "Narxi keyin kiritiladi" qatorlari
UNITS = ("шт", "кг", "м", "рулон", "упак")
# Справочник ustunlari (4 ta) va standart o'lchov birliklari
UNIT_KIND, MASTER, PRODUCT, RESPONSIBLE = "unit", "master", "product", "responsible"
DIR_HEADERS = ("Ед. изм.", "Мастера", "Наименование товара", "Ответственные")
DIR_NEEDLES = {UNIT_KIND: ("Ед. изм.", "ед. изм", "ед.изм"), MASTER: ("Мастера", "мастер"),
               PRODUCT: ("Наименование товара", "наименование"), RESPONSIBLE: ("Ответственные", "ответствен")}
DEFAULT_UNITS = ("шт", "кг", "м", "литр", "компл", "пачка", "рулон", "лист", "кв. м", "баллон", "банка", "мешок",
                 "ведро", "пара", "каробка")
RESET_FLAG = ".spravochnik_cleaned_v1"
RESET_MARKER = "zz_spravochnik_cleaned_v1"      # jadval darajasidagi ko'rinmas belgi (developerMetadata)

SUPPLIER = "supplier"
RECIPIENT = "recipient"


# ---------------- yordamchilar ----------------
def parse_number(value: Any) -> Optional[float]:
    """'700 000' -> 700000, '1,5' -> 1.5, '1,500,000' -> 1500000. Raqam bo'lmasa None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\xa0", "").replace(" ", "").strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if not re.search(r"\d", s):
        return None
    if "," in s and "." not in s:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def clean_text(value: str, limit: int = 120) -> str:
    """Foydalanuvchi matni: formulaga aylanib ketmasligi uchun boshidagi = + - @ olib tashlanadi."""
    s = re.sub(r"\s+", " ", str(value)).strip()
    s = s.lstrip("=+@").strip()
    return s[:limit]


def num_out(x: float) -> Any:
    """Jadvalga yoziladigan son: butun bo'lsa int."""
    r = round(float(x), 4)
    return int(r) if r == int(r) else r


def _key(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def _find_col(header: Sequence[Any], *needles: str) -> Optional[int]:
    """Sarlavhada `needles` dan biri bor birinchi ustun (0 dan boshlab)."""
    low = [_key(h) for h in header]
    for nd in needles:
        nd = nd.casefold()
        for i, h in enumerate(low):
            if h == nd:
                return i
    for nd in needles:
        nd = nd.casefold()
        for i, h in enumerate(low):
            if nd in h:
                return i
    return None


def _header_index(values: List[List[Any]], *needles: str) -> int:
    """Dastlabki 5 qator ichida sarlavha qatori (topilmasa 0)."""
    for i, row in enumerate(values[:5]):
        if _find_col(row, *needles) is not None:
            return i
    return 0


def _cell(row: Sequence[Any], i: Optional[int]) -> Any:
    return row[i] if i is not None and i < len(row) else ""


def to_date(value: Any, default_year: int) -> Optional[date]:
    """Jadval katagidagi sana: seriya raqami (46350), '2026-11-24', '24.11.2026' yoki 'Nov 24, 2026'."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if 20000 < value < 80000:
            return SERIAL_EPOCH + timedelta(days=int(value))
        return None
    return parse_cell_date(value, default_year)


def _col_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _width(header: Sequence[Any]) -> int:
    """Sarlavhadagi oxirgi to'ldirilgan ustungacha bo'lgan kenglik."""
    w = 0
    for i, v in enumerate(header):
        if str(v).strip():
            w = i + 1
    return w


# ---------------- ma'lumot turlari ----------------
@dataclass
class Product:
    name: str
    category: str
    unit: str
    pack: float
    stock: float
    avg_price: float
    min_stock: float
    status: str
    row: int                     # jadvaldagi qator raqami (1 dan)
    value: float = 0.0           # Сумма остатка (сум)

    @property
    def is_low(self) -> bool:
        return self.min_stock > 0 and self.stock <= self.min_stock


@dataclass
class InflowItem:
    name: str
    unit: str
    qty: float                   # asosiy birlikda
    price: Optional[float]       # 1 asosiy birlik narxi; None — "narxi keyin kiritiladi"
    supplier: str
    boxes: float = 0.0           # karobka bilan kiritilgan bo'lsa (faqat ko'rsatish uchun)
    pack: float = 1.0

    @property
    def total(self) -> float:
        return self.qty * (self.price or 0.0)


@dataclass
class UnpricedRow:
    """Приход dagi narxi kiritilmagan (bo'sh yoki 0) qator."""
    row: int
    day: Optional[date]
    name: str
    unit: str
    qty: float
    supplier: str


@dataclass
class OutflowItem:
    name: str
    unit: str
    qty: float
    purpose: str
    recipient: str
    stock_before: Optional[float] = None


@dataclass
class _FlowsCache:
    ts: float = 0.0
    last_price: Dict[str, float] = field(default_factory=dict)
    counts: Dict[str, Counter] = field(default_factory=dict)


class DuplicateError(Exception):
    pass


class NotFoundError(Exception):
    pass


class LastProductError(Exception):
    """Formulalar namunasi (2-qator) yo'qolmasligi uchun oxirgi mahsulotni o'chirib bo'lmaydi."""


# ---------------- servis ----------------
class WarehouseService:
    def __init__(self, sheets: SheetsClient, inventory_tab: str, inflow_tab: str, outflow_tab: str,
                 directory_tab: str, tz) -> None:
        self._sheets = sheets
        self.inventory_tab = inventory_tab
        self.inflow_tab = inflow_tab
        self.outflow_tab = outflow_tab
        self.directory_tab = directory_tab
        self._tz = tz
        self._catalog: List[Product] = []
        self._catalog_ts = 0.0
        self._flows = _FlowsCache()
        self._directory: Dict[str, List[str]] = {}
        self._directory_ts = 0.0
        self.cat_store = None          # storage.categories.CategoryStore (main.py da beriladi)
        self.norm_store = None         # storage.norms.NormMemory: oxirgi kiritilgan Мин. остаток (main.py)
        self.product_log = None        # storage.tracking.ProductLog: 🆕 yangi qo'shilgan mahsulotlar
        self.unpriced_store = None     # storage.tracking.UnpricedStore: narxsiz kirimlar nazorati
        self.other = None              # services.other_expenses.OtherExpensesService: 🧾 Boshqa xarajatlar

    # ---------- umumiy ----------
    def _ws(self, title: str):
        return self._sheets.spreadsheet.worksheet(title)

    def today(self) -> date:
        return datetime.now(self._tz).date()

    def today_str(self) -> str:
        """ISO sana: USER_ENTERED bilan yozilganda Google Sheets uni haqiqiy sana sifatida (2-qator formatida) oladi."""
        return self.today().isoformat()

    def invalidate(self) -> None:
        self._catalog_ts = 0.0
        self._flows.ts = 0.0
        self._directory_ts = 0.0

    @staticmethod
    def _values(ws) -> List[List[Any]]:
        return ws.get_all_values(value_render_option=ValueRenderOption.unformatted)

    # ---------- katalog (Остатки и Расходы) ----------
    @staticmethod
    def _inv_cols(header: Sequence[Any]) -> Dict[str, Optional[int]]:
        return {
            "name": _find_col(header, "Наименование товара", "наименование", "товар"),
            "category": _find_col(header, "Категория"),
            "unit": _find_col(header, "Ед. изм.", "ед. изм", "ед.изм", "единиц"),
            "pack": _find_col(header, "В упаковке (шт)", "в упаковке", "упаковк"),
            "stock": _find_col(header, "Остаток на складе", "остаток на складе"),
            "avg": _find_col(header, "Средняя цена (сум)", "средняя цена"),
            "min": _find_col(header, "Мин. остаток", "мин. норматив", "мин."),
            "status": _find_col(header, "Статус"),
            "value": _find_col(header, "Сумма остатка (сум)", "сумма остатка"),
        }

    def _read_catalog(self) -> List[Product]:
        values = self._values(self._ws(self.inventory_tab))
        h = _header_index(values, "Наименование товара", "наименование")
        if not values:
            return []
        cols = self._inv_cols(values[h])
        out: List[Product] = []
        for i, row in enumerate(values[h + 1:], start=h + 2):
            name = str(_cell(row, cols["name"])).strip()
            if not name:
                continue
            pack = parse_number(_cell(row, cols["pack"])) or 1.0
            out.append(Product(
                name=name,
                category=str(_cell(row, cols["category"])).strip(),
                unit=str(_cell(row, cols["unit"])).strip() or "шт",
                pack=pack if pack > 0 else 1.0,
                stock=parse_number(_cell(row, cols["stock"])) or 0.0,
                avg_price=parse_number(_cell(row, cols["avg"])) or 0.0,
                min_stock=parse_number(_cell(row, cols["min"])) or 0.0,
                status=str(_cell(row, cols["status"])).strip(),
                row=i,
                value=parse_number(_cell(row, cols["value"])) or 0.0,
            ))
        return out

    async def catalog(self, force: bool = False) -> List[Product]:
        if force or not self._catalog or time.monotonic() - self._catalog_ts > CACHE_TTL:
            self._catalog = await self._sheets.run(self._read_catalog)
            self._catalog_ts = time.monotonic()
        return self._catalog

    async def product(self, name: str, force: bool = False) -> Optional[Product]:
        k = _key(name)
        return next((p for p in await self.catalog(force) if _key(p.name) == k), None)

    async def categories(self) -> List[str]:
        seen: Dict[str, str] = {}
        for p in await self.catalog():
            if p.category and _key(p.category) not in seen:
                seen[_key(p.category)] = p.category
        return sorted(seen.values(), key=str.casefold)

    async def products_in(self, category: str) -> List[Product]:
        k = _key(category)
        return sorted((p for p in await self.catalog() if _key(p.category) == k), key=lambda p: p.name.casefold())

    # ---------- Приход / Расход: oxirgi narx va chastota ----------
    def _read_flows(self) -> _FlowsCache:
        cache = _FlowsCache(ts=time.monotonic(), counts={SUPPLIER: Counter(), RECIPIENT: Counter()})
        try:
            values = self._values(self._ws(self.inflow_tab))
            h = _header_index(values, "Наименование товара", "наименование")
            header = values[h] if values else []
            c_name = _find_col(header, "Наименование товара", "наименование")
            c_price = _find_col(header, "Цена за ед. (сум)", "цена за ед", "цена")
            c_sup = _find_col(header, "Поставщик / Примечание", "поставщик")
            for row in values[h + 1:]:
                name = str(_cell(row, c_name)).strip()
                if not name:
                    continue
                price = parse_number(_cell(row, c_price))
                if price:
                    cache.last_price[_key(name)] = price        # pastdagi qator = eng oxirgi
                sup = str(_cell(row, c_sup)).strip()
                if sup:
                    cache.counts[SUPPLIER][sup] += 1
        except WorksheetNotFound:
            logger.warning("'%s' varag'i topilmadi", self.inflow_tab)
        try:
            values = self._values(self._ws(self.outflow_tab))
            h = _header_index(values, "Наименование товара", "наименование")
            header = values[h] if values else []
            c_who = _find_col(header, "Ответственный", "ответствен")
            for row in values[h + 1:]:
                who = str(_cell(row, c_who)).strip()
                if who:
                    cache.counts[RECIPIENT][who] += 1
        except WorksheetNotFound:
            logger.warning("'%s' varag'i topilmadi", self.outflow_tab)
        return cache

    async def _flow_cache(self) -> _FlowsCache:
        if time.monotonic() - self._flows.ts > CACHE_TTL:
            self._flows = await self._sheets.run(self._read_flows)
        return self._flows

    async def last_price(self, name: str) -> Optional[float]:
        return (await self._flow_cache()).last_price.get(_key(name))

    # ---------- Справочник: 4 ustunli asosiy ma'lumotnoma ----------
    # Ед. изм. | Мастера | Наименование товара | Ответственные — hammasi botdan boshqariladi (Table4 ichida).
    @staticmethod
    def _dir_header_index(values: List[List[Any]]) -> int:
        return _header_index(values, "Наименование товара", "Мастера", "Ед. изм.", "Ответственные")

    @staticmethod
    def _dir_kind_col(header: Sequence[Any], kind: str) -> Optional[int]:
        needles = DIR_NEEDLES.get(kind)
        return _find_col(header, *needles) if needles else None

    def _read_directory(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {k: [] for k in (*DIR_NEEDLES, SUPPLIER, RECIPIENT)}
        try:
            values = self._ws(self.directory_tab).get_all_values()
        except WorksheetNotFound:
            logger.warning("'%s' varag'i topilmadi", self.directory_tab)
            return out
        if not values:
            return out
        h = self._dir_header_index(values)
        for kind in DIR_NEEDLES:
            col = self._dir_kind_col(values[h], kind)
            if col is None:
                continue
            seen = set()
            for row in values[h + 1:]:
                v = str(_cell(row, col)).strip()
                if v and _key(v) not in seen:
                    seen.add(_key(v))
                    out[kind].append(v)
        # 📤 Chiqim oluvchilari: Мастера (+ Ответственные). Do'konlar Справочник da saqlanmaydi — Приход tarixidan
        out[RECIPIENT] = merge_categories(out[MASTER], out[RESPONSIBLE])
        return out

    async def directory(self, kind: str) -> List[str]:
        if time.monotonic() - self._directory_ts > CACHE_TTL:
            self._directory = await self._sheets.run(self._read_directory)
            self._directory_ts = time.monotonic()
        return self._directory.get(kind, [])

    async def units(self) -> List[str]:
        """➕ Yangi mahsulot uchun o'lchov birliklari: Справочник «Ед. изм.» ustuni (bo'sh bo'lsa — standart)."""
        return await self.directory(UNIT_KIND) or list(DEFAULT_UNITS)

    async def top_names(self, kind: str, limit: int = 6) -> List[str]:
        """Справочник dagi nomlar (Мастера), Приход/Расход da eng ko'p ishlatilgani birinchi.
        Do'konlar (SUPPLIER) — Приход tarixidan."""
        names = await self.directory(kind)
        counts = (await self._flow_cache()).counts.get(kind, Counter())
        by_key = {_key(k): v for k, v in counts.items()}
        if not names:
            names = [n for n, _ in counts.most_common(limit)]
        order = {n: i for i, n in enumerate(names)}
        ranked = sorted(names, key=lambda n: (-by_key.get(_key(n), 0), order[n]))
        return ranked[:limit]

    def _dir_append_sync(self, kind: str, value: str) -> bool:
        """Qiymat Справочник ning tegishli ustuni oxiriga (Table4 ichida) yoziladi. False — allaqachon bor yoki
        ustun yo'q (ustunlar o'zboshimchalik bilan qo'shilmaydi: varaq 4 ustunli)."""
        try:
            ws = self._ws(self.directory_tab)
        except WorksheetNotFound:
            return False
        values = ws.get_all_values()
        h = self._dir_header_index(values)
        col = self._dir_kind_col(values[h], kind) if values else None
        if col is None:
            return False
        last = h
        for i, row in enumerate(values[h + 1:], start=h + 1):
            v = str(_cell(row, col)).strip()
            if v:
                if _key(v) == _key(value):
                    return False
                last = i
        target = last + 2
        if ws.row_count < target:
            ws.add_rows(target - ws.row_count + 50)
        ws.update([[value]], rowcol_to_a1(target, col + 1), value_input_option=ValueInputOption.raw)
        self._expand_table(ws, h, target)
        return True

    def _dir_remove_sync(self, kind: str, value: str) -> int:
        """Faqat shu ustundagi katak o'chiriladi va ustun yuqoriga suriladi (boshqa ustunlar joyida qoladi)."""
        try:
            ws = self._ws(self.directory_tab)
        except WorksheetNotFound:
            return 0
        values = ws.get_all_values()
        if not values:
            return 0
        h = self._dir_header_index(values)
        col = self._dir_kind_col(values[h], kind)
        if col is None:
            return 0
        rows = [i for i, row in enumerate(values) if i > h and _key(_cell(row, col)) == _key(value)]
        requests = [{"deleteRange": {"range": {"sheetId": ws.id, "startRowIndex": r, "endRowIndex": r + 1,
                                               "startColumnIndex": col, "endColumnIndex": col + 1},
                                     "shiftDimension": "ROWS"}} for r in sorted(rows, reverse=True)]
        if requests:
            self._sheets.spreadsheet.batch_update({"requests": requests})
        return len(requests)

    async def add_directory_value(self, kind: str, value: str) -> bool:
        ok = await self._sheets.run(self._dir_append_sync, kind, value)
        self._directory_ts = 0.0
        return ok

    async def delete_directory_value(self, kind: str, value: str) -> int:
        n = await self._sheets.run(self._dir_remove_sync, kind, value)
        self._directory_ts = 0.0
        return n

    async def remember_name(self, kind: str, name: str) -> None:
        """📤 Chiqim da yozilgan yangi oluvchi «Мастера» ga qo'shiladi (keyingi safar tugma bo'lib chiqadi).
        Do'konlar Справочник ga yozilmaydi (varaq 4 ustunli); ular Приход tarixidan taklif qilinadi."""
        if kind != RECIPIENT or not name or any(_key(n) == _key(name) for n in await self.directory(kind)):
            return
        try:
            await self.add_directory_value(MASTER, name)
        except Exception:
            logger.exception("Справочник ga yozib bo'lmadi")

    # ---------- bir martalik: Справочник ni tozalash va avto-import qilingan tovarlarni olib tashlash ----------
    def _reset_marker_exists(self) -> bool:
        meta = self._sheets.spreadsheet.fetch_sheet_metadata(
            params={"fields": "developerMetadata(metadataKey)"})
        return any(m.get("metadataKey") == RESET_MARKER for m in meta.get("developerMetadata", []) or [])

    def _reset_marker_write(self, stamp: str) -> None:
        self._sheets.spreadsheet.batch_update({"requests": [{"createDeveloperMetadata": {"developerMetadata": {
            "metadataKey": RESET_MARKER, "metadataValue": stamp, "location": {"spreadsheet": True},
            "visibility": "DOCUMENT"}}}]})

    def _reset_plan_sync(self) -> Dict[str, Any]:
        """Hech narsa yozmaydi: zaxira nusxa uchun hozirgi holat va o'chiriladigan qatorlar."""
        try:
            dvalues = self._ws(self.directory_tab).get_all_values()
        except WorksheetNotFound:
            dvalues = []
        history = set()
        for title in (self.inflow_tab, self.outflow_tab):
            try:
                values = self._ws(title).get_all_values()
            except WorksheetNotFound:
                continue
            if not values:
                continue
            hh = _header_index(values, "Наименование товара", "наименование")
            col = _find_col(values[hh], "Наименование товара", "наименование")
            history |= {_key(_cell(r, col)) for r in values[hh + 1:] if str(_cell(r, col)).strip()}
        inv = self._ws(self.inventory_tab).get_all_values()
        h = _header_index(inv, "Наименование товара", "наименование") if inv else 0
        name_col = self._inv_cols(inv[h] if inv else [])["name"]
        data_rows = [i for i, r in enumerate(inv) if i > h and str(_cell(r, name_col)).strip()]
        drop = [i for i in data_rows if _key(_cell(inv[i], name_col)) not in history]
        if drop and len(drop) == len(data_rows):
            drop = drop[1:]                     # formulalar namunasi (2-qator) yo'qolmasin
        return {"directory": dvalues, "inventory_header": inv[h] if inv else [],
                "inventory_dropped": [inv[i] for i in drop], "drop_rows": drop,
                "kept": [str(_cell(inv[i], name_col)) for i in data_rows if i not in drop]}

    def _reset_apply_sync(self, plan: Dict[str, Any], units: Sequence[str]) -> None:
        try:
            ws = self._ws(self.directory_tab)
        except WorksheetNotFound:
            ws = self._sheets.spreadsheet.add_worksheet(self.directory_tab, rows=200, cols=6)
        ws.batch_clear([f"A1:Z{max(ws.row_count, 2)}"])
        ws.update([list(DIR_HEADERS)], "A1:D1", value_input_option=ValueInputOption.raw)
        ws.update([[u] for u in units], f"A2:A{1 + len(units)}", value_input_option=ValueInputOption.raw)
        requests = []
        for tb in self._tables(ws.id):
            rng = tb.get("range") or {}
            if rng.get("startRowIndex", 0) == 0:
                requests.append({"updateTable": {"table": {"tableId": tb["tableId"], "range": {
                    "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1 + len(units),
                    "startColumnIndex": 0, "endColumnIndex": len(DIR_HEADERS)}}, "fields": "range"}})
        if requests:
            try:
                self._sheets.spreadsheet.batch_update({"requests": requests})
            except Exception:
                logger.warning("Table4 chegarasini yangilab bo'lmadi", exc_info=True)
        if plan["drop_rows"]:
            inv = self._ws(self.inventory_tab)
            self._sheets.spreadsheet.batch_update({"requests": [
                {"deleteDimension": {"range": {"sheetId": inv.id, "dimension": "ROWS",
                                               "startIndex": i, "endIndex": i + 1}}}
                for i in sorted(plan["drop_rows"], reverse=True)]})

    async def reset_directory_once(self, data_dir: Path) -> Optional[Dict[str, Any]]:
        """Bir martalik tozalash (`data/.spravochnik_cleaned_v1` belgisi bilan):
        1) avval `data/spravochnik_backup_*.json` ga to'liq zaxira nusxa;
        2) Справочник: 4 ustunli sarlavha, faqat standart o'lchov birliklari, Table4 = A1:D16;
        3) Остатки и Расходы: Приход/Расход da hech qanday harakati yo'q (avtomatik import qilingan) qatorlar
           o'chiriladi — haqiqiy harakati borlari qoladi.
        Belgi muvaffaqiyatdan keyingina yoziladi: xato bo'lsa, keyingi ishga tushishda qayta urinadi."""
        flag = data_dir / RESET_FLAG
        if flag.exists():
            return None
        # jadvalning o'zidagi ko'rinmas belgi: bot boshqa kompyuterda ishga tushsa ham tozalash QAYTARILMAYDI
        if await self._sheets.run(self._reset_marker_exists):
            data_dir.mkdir(parents=True, exist_ok=True)
            flag.write_text("marker found in spreadsheet", encoding="utf-8")
            return None
        plan = await self._sheets.run(self._reset_plan_sync)
        data_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(self._tz).strftime("%Y%m%d_%H%M%S")
        backup = data_dir / f"spravochnik_backup_{stamp}.json"
        backup.write_text(json.dumps({k: v for k, v in plan.items() if k != "drop_rows"}, ensure_ascii=False,
                                     indent=1), encoding="utf-8")
        await self._sheets.run(self._reset_apply_sync, plan, DEFAULT_UNITS)
        try:
            await self._sheets.run(self._reset_marker_write, stamp)
        except Exception:
            logger.warning("Jadvalga tozalash belgisini yozib bo'lmadi (mahalliy belgi yoziladi)", exc_info=True)
        flag.write_text(datetime.now(self._tz).isoformat(), encoding="utf-8")
        self.invalidate()
        logger.warning("Справочник tozalandi; Остатки dan %d ta harakatsiz tovar olib tashlandi (zaxira: %s)",
                       len(plan["drop_rows"]), backup.name)
        return {"dropped": len(plan["drop_rows"]), "kept": plan["kept"], "backup": backup.name}

    # ---------- qator qo'shish (formulalar bilan) ----------
    # ---------- Google Sheets jadvallari (Table1 / Table2 / Table3) ----------
    def _tables(self, sheet_id: int) -> List[Dict[str, Any]]:
        try:
            meta = self._sheets.spreadsheet.fetch_sheet_metadata(
                params={"fields": "sheets(properties(sheetId),tables(tableId,name,range))"})
        except Exception:
            logger.warning("Jadval (Table) ma'lumotini o'qib bo'lmadi", exc_info=True)
            return []
        for sh in meta.get("sheets", []):
            if sh.get("properties", {}).get("sheetId") == sheet_id:
                return sh.get("tables", []) or []
        return []

    def _expand_table(self, ws, header_row0: int, last_row: int, width: Optional[int] = None) -> None:
        """Sarlavhasi `header_row0` (0 dan) qatorda turgan jadvalni `last_row` (1 dan) qatorgacha va kerak bo'lsa
        `width` ustungacha kengaytiradi. Yangi qatorlar yashil chegara ichida qoladi."""
        requests = []
        for tb in self._tables(ws.id):
            rng = dict(tb.get("range") or {})
            if rng.get("startRowIndex", 0) != header_row0:
                continue
            old = dict(rng, sheetId=ws.id)
            new = dict(old)
            new["endRowIndex"] = max(rng.get("endRowIndex", 0), last_row)
            if width is not None:
                new["endColumnIndex"] = max(rng.get("endColumnIndex", 0), width)
            if new != old:
                requests.append({"updateTable": {"table": {"tableId": tb["tableId"], "range": new},
                                                 "fields": "range"}})
        if not requests:
            return
        try:
            self._sheets.spreadsheet.batch_update({"requests": requests})
        except Exception:
            logger.warning("Jadval (Table) chegarasini kengaytirib bo'lmadi", exc_info=True)

    @staticmethod
    def _bg_request(ws, row: int, width: int, color: Optional[Dict[str, float]]) -> Dict[str, Any]:
        """Qator fon rangi. color=None: qo'lda berilgan fon olib tashlanadi (jadval ranglari qoladi)."""
        cell = {"userEnteredFormat": {"backgroundColor": color}} if color else {}
        return {"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": row - 1, "endRowIndex": row,
                      "startColumnIndex": 0, "endColumnIndex": width},
            "cell": cell, "fields": "userEnteredFormat.backgroundColor"}}

    def _append(self, title: str, key_needles: Tuple[str, ...], items: List[Dict[int, Any]],
                formula_cols: Sequence[int], never_write: Sequence[int] = (),
                highlight: Sequence[int] = ()) -> List[int]:
        """`items`: har biri {ustun_indeksi: qiymat}. Kalit ustundagi oxirgi to'ldirilgan qatordan keyin yoziladi.
        `formula_cols` dagi ustunlar 2-qatordagi (namuna) formula bilan to'ldiriladi (copyPaste PASTE_FORMULA).
        `never_write` ustunlariga qiymat yozilmaydi. Qaytaradi: yozilgan qator raqamlari (1 dan)."""
        ws = self._ws(title)
        values = ws.get_all_values()
        h = _header_index(values, *key_needles)
        header = values[h] if values else []
        key_col = _find_col(header, *key_needles)
        if key_col is None:
            key_col = 0
        last = h
        for i, row in enumerate(values[h + 1:], start=h + 1):
            if str(_cell(row, key_col)).strip():
                last = i
        first = last + 2                                         # 1 dan boshlangan qator
        n = len(items)
        if ws.row_count < first + n - 1:
            ws.add_rows(first + n - 1 - ws.row_count + 20)
        width = max(_width(header), max((c + 1 for it in items for c in it), default=0))
        self._expand_table(ws, h, first + n - 1)                 # yangi qatorlar jadval ichida bo'lsin

        data = []
        for k, item in enumerate(items):
            r = first + k
            for c, v in item.items():
                if c in never_write:
                    continue
                data.append({"range": rowcol_to_a1(r, c + 1), "values": [[v]]})
        if data:
            ws.batch_update(data, value_input_option=ValueInputOption.user_entered)

        template_row = h + 2                                     # birinchi ma'lumot qatori (odatda 2)
        requests: List[Dict[str, Any]] = []
        if first > template_row and width:
            # 2-qatorning formati (sana ko'rinishi, raqam formati) va ochiladigan ro'yxatlari
            for paste in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
                requests.append({"copyPaste": {
                    "source": {"sheetId": ws.id, "startRowIndex": template_row - 1, "endRowIndex": template_row,
                               "startColumnIndex": 0, "endColumnIndex": width},
                    "destination": {"sheetId": ws.id, "startRowIndex": first - 1, "endRowIndex": first - 1 + n,
                                    "startColumnIndex": 0, "endColumnIndex": width},
                    "pasteType": paste}})
        if width:
            for k in range(n):
                # namunadagi sariq fon ko'chib o'tmasin; "narxi keyin" qatorlari sariq bo'yaladi
                requests.append(self._bg_request(ws, first + k, width, YELLOW if k in highlight else None))
        if formula_cols and first > template_row:
            tpl = ws.get(f"{template_row}:{template_row}", value_render_option=ValueRenderOption.formula)
            tpl_row = tpl[0] if tpl else []
            for c in formula_cols:
                f = _cell(tpl_row, c)
                if not (isinstance(f, str) and f.startswith("=")) or f.upper().startswith("=ARRAYFORMULA"):
                    continue
                # oldindan to'ldirilgan formulani buzmaslik uchun: bo'sh kataklargagina qo'yiladi
                if any(str(_cell(values[r - 1], c)).strip() for r in range(first, first + n) if r - 1 < len(values)):
                    continue
                requests.append({"copyPaste": {
                    "source": {"sheetId": ws.id, "startRowIndex": template_row - 1, "endRowIndex": template_row,
                               "startColumnIndex": c, "endColumnIndex": c + 1},
                    "destination": {"sheetId": ws.id, "startRowIndex": first - 1, "endRowIndex": first - 1 + n,
                                    "startColumnIndex": c, "endColumnIndex": c + 1},
                    "pasteType": "PASTE_FORMULA",
                }})
        if requests:
            self._sheets.spreadsheet.batch_update({"requests": requests})
        return list(range(first, first + n))

    def _inflow_sync(self, items: List[InflowItem], day: str) -> List[int]:
        ws = self._ws(self.inflow_tab)
        top = ws.get_values("A1:Z5")
        hidx = _header_index(top, "Наименование товара", "наименование")
        header = top[hidx] if top else []
        c = {
            "date": _find_col(header, "Дата"),
            "name": _find_col(header, "Наименование товара", "наименование"),
            "unit": _find_col(header, "Ед. изм.", "ед. изм", "ед.изм"),
            "qty": _find_col(header, "Количество", "кол"),
            "price": _find_col(header, "Цена за ед. (сум)", "цена за ед", "цена"),
            "total": _find_col(header, "Общая сумма (сум)", "общая сумма", "сумма"),
            "sup": _find_col(header, "Поставщик / Примечание", "поставщик", "примечание"),
        }
        defaults = {"date": 0, "name": 1, "unit": 2, "qty": 3, "price": 4, "total": 5, "sup": 6}
        c = {k: (v if v is not None else defaults[k]) for k, v in c.items()}
        rows = []
        later = []                                   # "narxi keyin kiritiladi": sariq bo'yaladi
        for k, it in enumerate(items):
            unpriced = not it.price
            if unpriced:
                later.append(k)
            rows.append({c["date"]: day, c["name"]: it.name, c["unit"]: it.unit, c["qty"]: num_out(it.qty),
                         c["price"]: "" if unpriced else num_out(round(it.price, 2)),
                         c["total"]: "" if unpriced else num_out(round(it.total, 2)),
                         c["sup"]: it.supplier})
        tpl = ws.get("{0}:{0}".format(hidx + 2), value_render_option=ValueRenderOption.formula)
        tpl_total = _cell(tpl[0] if tpl else [], c["total"])
        if isinstance(tpl_total, str) and tpl_total.startswith("="):
            # "Общая сумма" varaqda formula bilan hisoblanadi: qiymat emas, formula ko'chiriladi
            return self._append(self.inflow_tab, ("Наименование товара", "наименование"), rows,
                                formula_cols=[c["total"]], never_write=[c["total"]], highlight=later)
        return self._append(self.inflow_tab, ("Наименование товара", "наименование"), rows, formula_cols=[],
                            highlight=later)

    def _outflow_sync(self, items: List[OutflowItem], day: str) -> List[int]:
        top = self._ws(self.outflow_tab).get_values("A1:Z5")
        header = top[_header_index(top, "Наименование товара", "наименование")] if top else []
        c = {
            "date": _find_col(header, "Дата"),
            "name": _find_col(header, "Наименование товара", "наименование"),
            "qty": _find_col(header, "Количество расхода", "количество", "кол"),
            "purpose": _find_col(header, "Заказ / Цех / На какое изделие", "заказ", "цех", "изделие"),
            "who": _find_col(header, "Ответственный", "ответствен"),
        }
        defaults = {"date": 0, "name": 1, "qty": 2, "purpose": 3, "who": 4}
        c = {k: (v if v is not None else defaults[k]) for k, v in c.items()}
        sum_col = _find_col(header, "Сумма расхода (сум)", "сумма расхода", "сумма")
        sum_col = 5 if sum_col is None else sum_col
        rows = [{c["date"]: day, c["name"]: it.name, c["qty"]: num_out(it.qty), c["purpose"]: it.purpose,
                 c["who"]: it.recipient} for it in items]
        # "Сумма расхода" — varaq formulasi: hech qachon qiymat yozilmaydi, faqat formula ko'chiriladi
        return self._append(self.outflow_tab, ("Наименование товара", "наименование"), rows,
                            formula_cols=[sum_col], never_write=[sum_col])

    async def save_inflow(self, items: List[InflowItem]) -> List[int]:
        rows = await self._sheets.run(self._inflow_sync, items, self.today_str())
        self.invalidate()
        return rows

    async def save_outflow(self, items: List[OutflowItem]) -> List[int]:
        rows = await self._sheets.run(self._outflow_sync, items, self.today_str())
        self.invalidate()
        return rows

    async def low_stock(self, names: Sequence[str]) -> List[Product]:
        """Saqlangandan keyin: qoldig'i Мин. остаток ga teng yoki undan kam bo'lib qolganlari."""
        keys = {_key(n) for n in names}
        return [p for p in await self.catalog(force=True) if _key(p.name) in keys and p.is_low]

    # ---------- mahsulotlar katalogi ----------
    # ---------- 💰 narxi yo'q tovarlar ----------
    @staticmethod
    def _inflow_cols(header: Sequence[Any]) -> Dict[str, Optional[int]]:
        return {
            "date": _find_col(header, "Дата"),
            "name": _find_col(header, "Наименование товара", "наименование"),
            "unit": _find_col(header, "Ед. изм.", "ед. изм", "ед.изм"),
            "qty": _find_col(header, "Количество", "кол"),
            "price": _find_col(header, "Цена за ед. (сум)", "цена за ед", "цена"),
            "total": _find_col(header, "Общая сумма (сум)", "общая сумма", "сумма"),
            "sup": _find_col(header, "Поставщик / Примечание", "поставщик", "примечание"),
        }

    def _unpriced_sync(self) -> List[UnpricedRow]:
        try:
            values = self._values(self._ws(self.inflow_tab))
        except WorksheetNotFound:
            return []
        if not values:
            return []
        h = _header_index(values, "Наименование товара", "наименование")
        c = self._inflow_cols(values[h])
        if c["name"] is None or c["price"] is None:
            return []
        year = self.today().year
        out = []
        for i, row in enumerate(values[h + 1:], start=h + 2):
            name = str(_cell(row, c["name"])).strip()
            qty = parse_number(_cell(row, c["qty"])) or 0.0
            if not name or qty <= 0 or parse_number(_cell(row, c["price"])):
                continue
            out.append(UnpricedRow(row=i, day=to_date(_cell(row, c["date"]), year), name=name,
                                   unit=str(_cell(row, c["unit"])).strip() or "шт", qty=qty,
                                   supplier=str(_cell(row, c["sup"])).strip()))
        return out

    async def unpriced(self) -> List[UnpricedRow]:
        """Приход dagi narxi bo'sh yoki 0 bo'lgan qatorlar (eng eskisi birinchi)."""
        return await self._sheets.run(self._unpriced_sync)

    def _set_price_sync(self, row: int, name: str, price: float) -> float:
        ws = self._ws(self.inflow_tab)
        values = self._values(ws)
        h = _header_index(values, "Наименование товара", "наименование")
        header = values[h] if values else []
        c = self._inflow_cols(header)
        if c["price"] is None or row - 1 >= len(values) or row - 1 <= h \
                or _key(_cell(values[row - 1], c["name"])) != _key(name):
            raise NotFoundError(name)                 # qator surilgan yoki o'chirilgan
        qty = parse_number(_cell(values[row - 1], c["qty"])) or 0.0
        data = [{"range": rowcol_to_a1(row, c["price"] + 1), "values": [[num_out(round(price, 2))]]}]
        requests = [self._bg_request(ws, row, max(_width(header), 1), None)]      # sariq fon olib tashlanadi
        if c["total"] is not None:
            tpl = ws.get("{0}:{0}".format(h + 2), value_render_option=ValueRenderOption.formula)
            cur = ws.get("{0}:{0}".format(row), value_render_option=ValueRenderOption.formula)
            tpl_total = _cell(tpl[0] if tpl else [], c["total"])
            cur_total = _cell(cur[0] if cur else [], c["total"])
            if isinstance(cur_total, str) and cur_total.startswith("="):
                pass                                   # jami summa formulasi o'zi qayta hisoblaydi
            elif isinstance(tpl_total, str) and tpl_total.startswith("=") and row != h + 2:
                requests.append({"copyPaste": {        # formula 2-qatordan ko'chiriladi
                    "source": {"sheetId": ws.id, "startRowIndex": h + 1, "endRowIndex": h + 2,
                               "startColumnIndex": c["total"], "endColumnIndex": c["total"] + 1},
                    "destination": {"sheetId": ws.id, "startRowIndex": row - 1, "endRowIndex": row,
                                    "startColumnIndex": c["total"], "endColumnIndex": c["total"] + 1},
                    "pasteType": "PASTE_FORMULA"}})
            else:
                data.append({"range": rowcol_to_a1(row, c["total"] + 1),
                             "values": [[num_out(round(qty * price, 2))]]})
        ws.batch_update(data, value_input_option=ValueInputOption.user_entered)
        self._sheets.spreadsheet.batch_update({"requests": requests})
        return qty * price

    async def set_price(self, row: int, name: str, price: float) -> float:
        """Narxi keyin kiritilgan qatorga narx yoziladi, jami summa yangilanadi, sariq belgi olinadi."""
        total = await self._sheets.run(self._set_price_sync, row, name, price)
        self.invalidate()
        return total

    # ---------- kun bo'yicha yozuvlar ----------
    def _day_counts_sync(self, day: date) -> Tuple[int, int]:
        counts = []
        for title in (self.inflow_tab, self.outflow_tab):
            try:
                values = self._values(self._ws(title))
            except WorksheetNotFound:
                counts.append(0)
                continue
            if not values:
                counts.append(0)
                continue
            h = _header_index(values, "Наименование товара", "наименование")
            c_date = _find_col(values[h], "Дата")
            c_name = _find_col(values[h], "Наименование товара", "наименование")
            counts.append(sum(1 for row in values[h + 1:]
                              if str(_cell(row, c_name)).strip() and to_date(_cell(row, c_date), day.year) == day))
        return counts[0], counts[1]

    async def day_counts(self, day: date) -> Tuple[int, int]:
        """(Приход qatorlari, Расход qatorlari) soni — `day` sanasi bilan."""
        return await self._sheets.run(self._day_counts_sync, day)

    async def stock_value(self, fresh: bool = True) -> float:
        """Ombordagi jami qoldiq qiymati (Сумма остатка, bo'lmasa qoldiq × o'rtacha narx).
        fresh=False: 60 soniyalik katalog keshidan (Dashboard)."""
        return sum(p.value or p.stock * p.avg_price for p in await self.catalog(force=fresh))

    async def category_values(self, out_rows: Sequence[Any]) -> Dict[str, Tuple[float, float]]:
        """Kategoriya -> (ombordagi qoldiq qiymati, davrdagi sarf qiymati).
        `out_rows`: flows_store dagi Расход yozuvlari (label, qty, amount). Сумма расхода bo'lmasa —
        miqdor × o'rtacha narx."""
        catalog = await self.catalog()
        by_name = {_key(p.name): p for p in catalog}
        out: Dict[str, List[float]] = {}
        for p in catalog:
            agg = out.setdefault(p.category or "", [0.0, 0.0])
            agg[0] += p.value or p.stock * p.avg_price
        for r in out_rows:
            p = by_name.get(_key(getattr(r, "label", "")))
            value = float(getattr(r, "amount", 0) or 0) or float(getattr(r, "qty", 0) or 0) * (p.avg_price if p else 0.0)
            agg = out.setdefault(p.category if p and p.category else "", [0.0, 0.0])
            agg[1] += value
        return {k: (v[0], v[1]) for k, v in sorted(out.items(), key=lambda kv: -kv[1][0])}

    async def low_items(self) -> List[Product]:
        return [p for p in await self.catalog() if p.is_low]

    # ---------- kategoriyalar ----------
    async def sheet_categories(self) -> List[str]:
        """`Категория` ustunida uchraydigan nomlar (katalogdagi kabi yozilishida)."""
        return await self.categories()

    async def all_categories(self) -> List[str]:
        """Umumiy ro'yxat (saqlangan tartibda) + jadvalda uchragan, lekin ro'yxatda yo'q kategoriyalar."""
        stored = await self.cat_store.names() if self.cat_store is not None else []
        return merge_categories(stored, await self.categories())

    async def add_category(self, name: str) -> bool:
        if self.cat_store is None:
            return False
        return await self.cat_store.add(name)

    async def delete_category(self, name: str) -> None:
        if self.cat_store is not None:
            await self.cat_store.delete(name)

    async def category_counts(self) -> Dict[str, int]:
        """kategoriya (casefold) -> shu kategoriyadagi mahsulotlar soni."""
        out: Dict[str, int] = {}
        for p in await self.catalog(force=True):
            if p.category:
                out[_key(p.category)] = out.get(_key(p.category), 0) + 1
        return out

    def _rename_category_sync(self, old: str, new: str) -> int:
        ws = self._ws(self.inventory_tab)
        values = ws.get_all_values()
        if not values:
            return 0
        h = _header_index(values, "Наименование товара", "наименование")
        col = self._inv_cols(values[h])["category"]
        if col is None:
            return 0
        data = [{"range": rowcol_to_a1(i + 1, col + 1), "values": [[new]]}
                for i, row in enumerate(values) if i > h and _key(_cell(row, col)) == _key(old)]
        if data:
            ws.batch_update(data, value_input_option=ValueInputOption.raw)
        return len(data)

    async def rename_category(self, old: str, new: str) -> int:
        """Ro'yxatda va `Категория` ustunidagi barcha mos kataklarda yangi nom. O'zgargan kataklar soni."""
        n = await self._sheets.run(self._rename_category_sync, old, new)
        if self.cat_store is not None:
            await self.cat_store.rename(old, new)
        self.invalidate()
        return n

    # ---------- ustunlar migratsiyasi (ishlab turgan jadvalni buzmasdan) ----------
    def _add_columns(self, ws, values: List[List[Any]], h: int, names: Sequence[str]) -> List[int]:
        """Yetishmagan ustunlarni jadval OXIRIGA qo'shadi (o'rtaga hech qachon). Qo'shilgan ustun indekslari."""
        if not names:
            return []
        header = values[h] if values else []
        width = _width(header)
        added = [width + k for k in range(len(names))]
        if ws.col_count < width + len(names):
            ws.add_cols(width + len(names) - ws.col_count)
        ws.batch_update([{"range": rowcol_to_a1(h + 1, col + 1), "values": [[name]]}
                         for col, name in zip(added, names)], value_input_option=ValueInputOption.raw)
        last = max([h + 1] + [i + 1 for i, r in enumerate(values) if any(str(v).strip() for v in r)])
        self._expand_table(ws, h, last, width + len(names))
        return added

    def _migrate_sync(self) -> List[str]:
        done: List[str] = []
        # --- Остатки и Расходы ---
        inv = self._ws(self.inventory_tab)
        values = inv.get_all_values()
        if not values:
            return done
        h = _header_index(values, "Наименование товара", "наименование")
        header = list(values[h])
        if _find_col(header, "Мин. остаток") is None:
            for i, v in enumerate(header):
                if _key(v).startswith("мин. норматив"):
                    inv.update_cell(h + 1, i + 1, "Мин. остаток")              # joyida qayta nomlanadi
                    header[i] = "Мин. остаток"
                    done.append(f"{self.inventory_tab}: Мин. остаток")
                    break
        wanted = (("Категория", ("Категория",)),
                  ("В упаковке (шт)", ("В упаковке (шт)", "в упаковке")),
                  ("Сумма расхода (сум)", ("Сумма расхода (сум)", "сумма расхода")))
        missing = [name for name, needles in wanted if _find_col(header, *needles) is None]
        added = self._add_columns(inv, values, h, missing)
        cols = self._inv_cols(header)
        out_qty = _find_col(header, "Всего Расход (Кол-во)", "всего расход")
        data_rows = [i + 1 for i, r in enumerate(values) if i > h and str(_cell(r, cols["name"])).strip()]
        for name, col in zip(missing, added):
            done.append(f"{self.inventory_tab}: {name}")
            if name == "Сумма расхода (сум)" and out_qty is not None and cols["avg"] is not None and data_rows:
                q, a = _col_letter(out_qty), _col_letter(cols["avg"])
                inv.batch_update([{"range": rowcol_to_a1(r, col + 1), "values": [[f"=IFERROR({q}{r}*{a}{r}, 0)"]]}
                                  for r in data_rows], value_input_option=ValueInputOption.user_entered)

        # --- Расход: Сумма расхода (сум) = miqdor × o'rtacha narx (Остатки и Расходы dan) ---
        try:
            out = self._ws(self.outflow_tab)
        except WorksheetNotFound:
            return done
        ovalues = out.get_all_values()
        if not ovalues:
            return done
        oh = _header_index(ovalues, "Наименование товара", "наименование")
        oheader = ovalues[oh]
        if _find_col(oheader, "Сумма расхода (сум)", "сумма расхода") is None:
            col = self._add_columns(out, ovalues, oh, ["Сумма расхода (сум)"])[0]
            done.append(f"{self.outflow_tab}: Сумма расхода (сум)")
            c_name = _find_col(oheader, "Наименование товара", "наименование")
            c_qty = _find_col(oheader, "Количество расхода", "количество", "кол")
            if None not in (c_name, c_qty, cols["name"], cols["avg"]):
                n, q = _col_letter(c_name), _col_letter(c_qty)
                inv_name, inv_avg = _col_letter(cols["name"]), _col_letter(cols["avg"])
                tab = self.inventory_tab.replace("'", "''")
                rows = [i + 1 for i, r in enumerate(ovalues) if i > oh and str(_cell(r, c_name)).strip()] or [oh + 2]
                formula = "=IFERROR({q}{r}*INDEX('{tab}'!{avg}:{avg}, MATCH({n}{r}, '{tab}'!{nm}:{nm}, 0)), 0)"
                out.batch_update([{"range": rowcol_to_a1(r, col + 1),
                                   "values": [[formula.format(q=q, n=n, r=r, tab=tab, avg=inv_avg, nm=inv_name)]]}
                                  for r in rows], value_input_option=ValueInputOption.user_entered)
        return done

    async def migrate_schema(self) -> List[str]:
        """Ishga tushganda: yetishmagan ustunlar jadval oxiriga qo'shiladi. Qayta chaqirish xavfsiz (idempotent)."""
        done = await self._sheets.run(self._migrate_sync)
        if done:
            logger.info("Jadval ustunlari yangilandi: %s", "; ".join(done))
        self.invalidate()
        return done

    def _add_products_rows(self, header: Sequence[Any],
                           products: Sequence[Tuple[str, str, str, float, float]]) -> List[int]:
        """Остатки и Расходы ga bir nechta mahsulot: (nom, kategoriya, birlik, qadoqda, min. qoldiq).
        Kiritilmagan ustunlar (Всего Приход, Остаток, Статус ...) 2-qatordagi formulalar bilan to'ldiriladi,
        format / ochiladigan ro'yxatlar ko'chiriladi va Table3 chegarasi kengaytiriladi. Boshlang'ich qoldiq = 0."""
        cols = self._inv_cols(header)
        items: List[Dict[int, Any]] = []
        for name, category, unit, pack, min_stock in products:
            item: Dict[int, Any] = {}
            for key, val in (("name", name), ("category", category), ("unit", unit),
                             ("pack", num_out(pack)), ("min", num_out(min_stock))):
                if cols[key] is not None:
                    item[cols[key]] = val
            if cols["name"] is None:
                item[0] = name
            items.append(item)
        input_cols = set(items[0]) if items else set()
        formula_cols = [i for i in range(len(header)) if i not in input_cols]
        return self._append(self.inventory_tab, ("Наименование товара", "наименование"), items,
                            formula_cols=formula_cols)

    def _add_product_sync(self, name: str, category: str, unit: str, pack: float, min_stock: float) -> int:
        ws = self._ws(self.inventory_tab)
        values = ws.get_all_values()
        h = _header_index(values, "Наименование товара", "наименование")
        header = values[h] if values else []
        cols = self._inv_cols(header)
        for row in values[h + 1:]:
            if _key(_cell(row, cols["name"])) == _key(name):
                raise DuplicateError(name)
        row = self._add_products_rows(header, [(name, category, unit, pack, min_stock)])[0]
        try:
            self._dir_append_sync(PRODUCT, name)   # Справочник «Наименование товара» ga ham (Table4 ichida)
        except Exception:
            logger.warning("Yangi mahsulotni Справочник ga yozib bo'lmadi", exc_info=True)
        return row

    # ---------- ✏️ mahsulot ma'lumotlarini tahrirlash ----------
    def _update_fields_sync(self, name: str, fields: Dict[str, Any]) -> None:
        """Остатки и Расходы dagi mahsulot qatori: unit / category / min (ustunlar sarlavha bo'yicha topiladi)."""
        ws = self._ws(self.inventory_tab)
        values = ws.get_all_values()
        h = _header_index(values, "Наименование товара", "наименование")
        cols = self._inv_cols(values[h] if values else [])
        row = next((i + 1 for i, r in enumerate(values) if i > h and _key(_cell(r, cols["name"])) == _key(name)), None)
        if row is None:
            raise NotFoundError(name)
        data = []
        for key, value in fields.items():
            if value is None or cols.get(key) is None:
                continue
            data.append({"range": rowcol_to_a1(row, cols[key] + 1),
                         "values": [[num_out(value) if key == "min" else value]]})
        if data:
            ws.batch_update(data, value_input_option=ValueInputOption.user_entered)

    async def update_product(self, name: str, unit: Optional[str] = None, category: Optional[str] = None,
                             min_stock: Optional[float] = None) -> None:
        """Birlik, kategoriya yoki Мин. остаток. Yangi birlik Справочник «Ед. изм.» ro'yxatiga ham qo'shiladi."""
        await self._sheets.run(self._update_fields_sync, name,
                               {"unit": unit, "category": category, "min": min_stock})
        if unit:
            try:
                await self.add_directory_value(UNIT_KIND, unit)      # bor bo'lsa — hech narsa qilinmaydi
            except Exception:
                logger.warning("Birlikni Справочник ga yozib bo'lmadi", exc_info=True)
        self.invalidate()

    async def add_product(self, name: str, category: str, unit: str, pack: float, min_stock: float) -> int:
        row = await self._sheets.run(self._add_product_sync, name, category, unit, pack, min_stock)
        self.invalidate()
        return row

    def _rename_sync(self, old: str, new: str) -> Dict[str, int]:
        """Nom BARCHA varaqlarda bir vaqtda almashtiriladi (SUMIFS formulalari buzilmaydi)."""
        ok, nk = _key(old), _key(new)
        changed: Dict[str, int] = {}
        inv = self._ws(self.inventory_tab)
        inv_values = inv.get_all_values()
        h = _header_index(inv_values, "Наименование товара", "наименование")
        name_col = self._inv_cols(inv_values[h] if inv_values else [])["name"]
        if any(_key(_cell(r, name_col)) == nk for r in inv_values[h + 1:]) and nk != ok:
            raise DuplicateError(new)
        targets = [(self.inventory_tab, True), (self.inflow_tab, True), (self.outflow_tab, True),
                   (self.directory_tab, False)]
        for title, product_col_only in targets:
            try:
                ws = inv if title == self.inventory_tab else self._ws(title)
            except WorksheetNotFound:
                continue
            values = inv_values if title == self.inventory_tab else ws.get_all_values()
            hh = _header_index(values, "Наименование товара", "наименование")
            col = _find_col(values[hh], "Наименование товара", "наименование") if (values and product_col_only) else None
            data = []
            for ri, row in enumerate(values):
                if product_col_only and ri <= hh:
                    continue
                for ci, v in enumerate(row):
                    if product_col_only and col is not None and ci != col:
                        continue
                    if v and _key(v) == ok:
                        data.append({"range": rowcol_to_a1(ri + 1, ci + 1), "values": [[new]]})
            if data:
                ws.batch_update(data, value_input_option=ValueInputOption.raw)
            changed[title] = len(data)
        return changed

    async def rename_product(self, old: str, new: str) -> Dict[str, int]:
        res = await self._sheets.run(self._rename_sync, old, new)
        self.invalidate()
        return res

    def _delete_sync(self, name: str) -> int:
        ws = self._ws(self.inventory_tab)
        values = ws.get_all_values()
        h = _header_index(values, "Наименование товара", "наименование")
        col = self._inv_cols(values[h] if values else [])["name"]
        rows = [i + 1 for i, r in enumerate(values) if i > h and _key(_cell(r, col)) == _key(name)]
        if not rows:
            raise NotFoundError(name)
        data_rows = [i for i, r in enumerate(values) if i > h and str(_cell(r, col)).strip()]
        if len(data_rows) <= 1:
            raise LastProductError(name)
        row = rows[0]
        self._sheets.spreadsheet.batch_update({"requests": [{"deleteDimension": {"range": {
            "sheetId": ws.id, "dimension": "ROWS", "startIndex": row - 1, "endIndex": row}}}]})
        try:
            self._dir_remove_sync(PRODUCT, name)    # Справочник dan ham
        except Exception:
            logger.warning("Mahsulotni Справочник dan o'chirib bo'lmadi", exc_info=True)
        return row

    async def delete_product(self, name: str) -> int:
        row = await self._sheets.run(self._delete_sync, name)
        self.invalidate()
        return row
