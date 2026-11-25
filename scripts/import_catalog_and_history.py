"""Bir martalik migratsiya: 63 ta standart mahsulot + manba jadvaldan Приход / Расход tarixi.

Ishga tushirish (loyiha ildizidan):
    python scripts/import_catalog_and_history.py            # quruq yurish: hech narsa yozmaydi, faqat hisobot
    python scripts/import_catalog_and_history.py --apply    # haqiqiy yozish

Nima qiladi:
1. 63 ta standart mahsulotdan yetishmaganlarini «Остатки и Расходы» (2-qator formulalari, formati va Table3
   chegarasi bilan) va «Справочник» → «Наименование товара» ga qo'shadi. Mavjud mahsulotlarga tegilmaydi.
2. Manba jadvalning «Приход» / «Расход» qatorlaridan faqat 63 ta mahsulotga mos kelganlarini (qat'iy alias
   lug'ati orqali) oladi, qolganlarini o'tkazib yuborib, hisobotda ko'rsatadi. Maqsad varaqdagi mavjud qatorlar
   bilan birga sana bo'yicha tartiblanadi: yangi qatorlar 2-qatordan boshlab qo'yiladi, bugungi (bot kiritgan)
   qatorlar pastda qoladi. Formulali ustunlar (Общая сумма, Сумма расхода) varaq formulasi bilan to'ldiriladi.
3. Narxi 0 yoki bo'sh bo'lgan import qilingan Приход qatorlari SQLite `unpriced_items` nazoratiga yoziladi.

Xavfsizlik: yozishdan oldin maqsad varaqlarning zaxira nusxasi data/backups/ ga saqlanadi; jadval darajasida
ko'rinmas belgi (developerMetadata) qo'yiladi — skript ikkinchi marta yozmaydi. Mavjud qatorlar bilan bir xil
bo'lgan manba qatorlari (sana + nom + miqdor + narx / oluvchi) takror qo'shilmaydi.
Hech qanday maxfiy ma'lumot (.env, credentials) chop etilmaydi.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import gspread  # noqa: E402
from gspread.utils import ValueInputOption, ValueRenderOption, rowcol_to_a1  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.services.sheets import SheetsClient  # noqa: E402
from app.services.warehouse import (PRODUCT, UNIT_KIND, YELLOW, WarehouseService, _cell, _find_col,  # noqa: E402
                                    parse_number)

SOURCE_ID = "1zuEaNoVhLPVvO_NhqIB46xSWEpa3pZ6AR7d9GBql0zI"
MARKER = "zz_history_import_v1"
SERIAL_EPOCH = date(1899, 12, 30)

# (nom, birlik, kategoriya, Мин. норматив)
CATALOG: Tuple[Tuple[str, str, str, float], ...] = (
    ("Ручка «Пятак»", "шт", "Фурнитура", 100),
    ("Ручка 128 мм черная", "шт", "Фурнитура", 50),
    ("Ручка 160 мм", "шт", "Фурнитура", 50),
    ("Ручка 192 мм", "шт", "Фурнитура", 30),
    ("Салазки 35 см", "пара", "Фурнитура", 30),
    ("Салазки 40 см", "пара", "Фурнитура", 40),
    ("Салазки 45 см", "пара", "Фурнитура", 50),
    ("Салазки 50 см", "пара", "Фурнитура", 30),
    ("Саморез 19 мм", "кг", "Крепеж / Метизы", 5),
    ("Саморез 20 мм", "кг", "Крепеж / Метизы", 5),
    ("Саморез 30 мм", "кг", "Крепеж / Метизы", 10),
    ("Скоба F15", "пачка", "Крепеж / Метизы", 5),
    ("Скоба F25", "пачка", "Крепеж / Метизы", 5),
    ("Скоба F30", "пачка", "Крепеж / Метизы", 5),
    ("Скоба J10 (0.6)", "пачка", "Крепеж / Метизы", 5),
    ("Скоба J10 (0.8)", "пачка", "Крепеж / Метизы", 5),
    ("Скоба J10 (10)", "пачка", "Крепеж / Метизы", 5),
    ("Скоба невидимка", "пачка", "Крепеж / Метизы", 3),
    ("Скотч прозрачный", "шт", "Расходники", 15),
    ("Скотч малярный", "шт", "Расходники", 15),
    ("Скотч двусторонний", "шт", "Расходники", 10),
    ("Клей ПВА (850 гр)", "шт", "Клей и Химия", 10),
    ("Клей Акфикс (Akfix)", "шт", "Клей и Химия", 10),
    ("Клей для кромки (гранулы)", "кг", "Клей и Химия", 25),
    ("Герметик силиконовый", "шт", "Клей и Химия", 5),
    ("Пена монтажная «Титан»", "шт", "Клей и Химия", 5),
    ("Маска защитная KN95", "шт", "Расходники", 30),
    ("Перчатки рабочие", "пара", "Расходники", 50),
    ("Шкурка сеточная P220", "шт", "Расходники", 30),
    ("Шкурка сеточная P400", "шт", "Расходники", 30),
    ("Шкурка круглая P320", "шт", "Расходники", 50),
    ("Кромка ПВХ 1×19", "рулон", "Поролон и Обивка", 3),
    ("Кромка ПВХ 2×19", "рулон", "Поролон и Обивка", 3),
    ("Кромка ПВХ 1×35", "рулон", "Поролон и Обивка", 2),
    ("Кромка ПВХ 2×35", "рулон", "Поролон и Обивка", 2),
    ("Соединитель мебельный", "шт", "Крепеж / Метизы", 100),
    ("Евростяжка (Конфирмат)", "шт", "Крепеж / Метизы", 200),
    ("Механизм барный", "шт", "Фурнитура", 10),
    ("Механизм купе (комплект)", "компл", "Фурнитура", 5),
    ("Трубодержатель 25 мм (фланец)", "шт", "Фурнитура", 30),
    ("Труба хромированная 25 мм", "шт", "Фурнитура", 10),
    ("Луза (заглушка кабель-канала)", "шт", "Фурнитура", 30),
    ("Замок мебельный длинный", "шт", "Фурнитура", 20),
    ("Замок мебельный короткий", "шт", "Фурнитура", 20),
    ("Замок центральный", "шт", "Фурнитура", 15),
    ("Уголок мебельный пластиковый", "шт", "Крепеж / Метизы", 200),
    ("Уголок металлический", "шт", "Крепеж / Метизы", 100),
    ("Гвозди строительные", "кг", "Крепеж / Метизы", 3),
    ("Ножка «Бинго»", "шт", "Фурнитура", 50),
    ("Ножка мебельная 6 см", "шт", "Фурнитура", 40),
    ("Ножка мебельная 8 см", "шт", "Фурнитура", 40),
    ("Ножка мебельная 10 см", "шт", "Фурнитура", 40),
    ("Ножка мебельная 30 см", "шт", "Фурнитура", 20),
    ("Ролик мебельный с тормозом", "шт", "Фурнитура", 20),
    ("Ролик мебельный без тормоза", "шт", "Фурнитура", 20),
    ("Петля вкладная (горбатая)", "шт", "Фурнитура", 50),
    ("Петля полунакладная (полугорбатая)", "шт", "Фурнитура", 50),
    ("Петля накладная (прямая)", "шт", "Фурнитура", 100),
    ("Пистолет продувочный воздушный", "шт", "Инструменты", 2),
    ("Подпятник мебельный", "шт", "Фурнитура", 200),
    ("Полкодержатель", "шт", "Фурнитура", 200),
    ("Шпингалет мебельный", "шт", "Фурнитура", 20),
    ("Крючок мебельный", "шт", "Фурнитура", 50),
)

# Manba jadvaldagi variant / xato yozilgan nom -> standart nom. Faqat aniq moslik (normalizatsiyadan keyin).
ALIASES: Dict[str, str] = {}


def _alias(target: str, *variants: str) -> None:
    for v in variants:
        ALIASES[v] = target


for _n in ("35", "40", "45", "50"):
    _alias(f"Салазки {_n} см", f"Саляска {_n}", f"Саласка {_n}", f"Салазка {_n}", f"Салазки {_n}",
           f"Саляска {_n} см", f"Саласка {_n} см")
for _n in ("19", "20", "30"):
    _alias(f"Саморез {_n} мм", f"Саморез {_n}", f"Саморез {_n}мм")
_alias("Саморез 30 мм", "Саморез черный 30")                      # 63 tadan yagona 30 mm li samorez
_alias("Скоба J10 (0.6)", "Скоба J10/0.6", "Скоба J10 0.6")
_alias("Скоба J10 (0.8)", "Скоба J10/0.8", "Скоба J10 0.8")
_alias("Скоба J10 (10)", "Скоба J10/10", "Скоба J10 10")
_alias("Скотч прозрачный", "Скотч")
_alias("Скотч малярный", "Малярный скотч")
_alias("Скотч двусторонний", "Скотч двухсторонный", "Скотч двухсторонний", "Двусторонний скотч")
_alias("Клей ПВА (850 гр)", "ПВА Клей 850гр", "ПВА клей 850 гр", "Клей ПВА 850гр", "Клей ПВА")
_alias("Клей для кромки (гранулы)", "Клей для кромки")
_alias("Маска защитная KN95", "Маска KN95")
_alias("Перчатки рабочие", "Перчатки")
_alias("Шкурка сеточная P220", "Шкурка сеткали P220")
_alias("Шкурка сеточная P400", "Шкурка сеткали P400", "Шкурка P400")   # P400 — faqat to'rli shkurka
_alias("Шкурка круглая P320", "Шкурка думалок P320")
_alias("Кромка ПВХ 1×19", "Кромка 1/19", "Кромка 19/1")
_alias("Кромка ПВХ 2×19", "Кромка 2/19", "Кромка 19/2")
_alias("Кромка ПВХ 1×35", "Кромка 1/35", "Кромка 35/1")
_alias("Кромка ПВХ 2×35", "Кромка 2/35", "Кромка 35/2")
_alias("Соединитель мебельный", "Соединитель")
_alias("Евростяжка (Конфирмат)", "Евростяжка", "Конфирмат")
_alias("Механизм барный", "Барный механизм")
_alias("Механизм купе (комплект)", "Механизм купе", "Кюпе механизм", "Купе механизм")
_alias("Трубодержатель 25 мм (фланец)", "Кронштейн для трубы 25", "Кронштейн для трубы 25 (Трубодержатель 25)",
       "Трубодержатель 25")
_alias("Труба хромированная 25 мм", "Труба карнизная 25")
_alias("Луза (заглушка кабель-канала)", "Луза")
_alias("Замок мебельный длинный", "Мебельный замок длинный")
_alias("Замок мебельный короткий", "Мебельный замок короткий")
_alias("Замок центральный", "Центральный замок")
_alias("Уголок мебельный пластиковый", "Мебельный уголок")
_alias("Уголок металлический", "Подкладочный уголок", "Подкладочный уголок (Металл угольник)")
_alias("Гвозди строительные", "Мих (Гвозди)", "Мих", "Гвозди")
for _n in ("6", "8", "10", "30"):
    _alias(f"Ножка мебельная {_n} см", f"Ножка {_n} см", f"Ножка {_n}см")
_alias("Ножка «Бинго»", "Ножка Бинго")
_alias("Ролик мебельный с тормозом", "Ножка-ролик", "Ножка ролик")
_alias("Ролик мебельный без тормоза", "Ножка ролик без тормоза", "Ножка-ролик без тормоза")
_alias("Петля вкладная (горбатая)", "Петля горбатая")
_alias("Петля полунакладная (полугорбатая)", "Петля полугорбатая")
_alias("Петля накладная (прямая)", "Петля прямая")
_alias("Пистолет продувочный воздушный", "Пистолет воздушный")
_alias("Подпятник мебельный", "Подпятник")
_alias("Шпингалет мебельный", "Шпингалет")
_alias("Крючок мебельный", "Кручок")


def norm(s: Any) -> str:
    return " ".join(str(s).replace("ё", "е").replace("Ё", "Е").split()).casefold()


CANON = {norm(n): n for n, *_ in CATALOG}
LOOKUP = dict(CANON)
for _v, _t in ALIASES.items():
    assert _t in {n for n, *_ in CATALOG}, _t
    LOOKUP.setdefault(norm(_v), _t)
UNIT_OF = {n: u for n, u, _c, _m in CATALOG}


def canonical(name: Any) -> Optional[str]:
    return LOOKUP.get(norm(name))


def to_day(v: Any) -> Optional[date]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return SERIAL_EPOCH + timedelta(days=int(v))
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def num(v: Any) -> Any:
    """Raqamni qanday bo'lsa shunday saqlaydi (0 ham); bo'sh -> ''."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if float(v).is_integer() else v
    p = parse_number(v)
    if p is None:
        return str(v).strip()
    return int(p) if float(p).is_integer() else p


# ---------------- varaq ustunlari ----------------
IN_COLS = {"date": ("Дата",), "name": ("Наименование товара", "наименование"), "unit": ("Ед. изм.", "ед. изм"),
           "qty": ("Количество", "кол"), "price": ("Цена за ед. (сум)", "цена за ед", "цена"),
           "total": ("Общая сумма (сум)", "общая сумма"), "note": ("Поставщик / Примечание", "поставщик")}
OUT_COLS = {"date": ("Дата",), "name": ("Наименование товара", "наименование"),
            "qty": ("Количество расхода", "количество"), "purpose": ("Заказ / Цех / На какое изделие", "заказ"),
            "who": ("Ответственный", "ответствен"), "sum": ("Сумма расхода (сум)", "сумма расхода")}


def cols_of(header: Sequence[Any], spec: Dict[str, Tuple[str, ...]]) -> Dict[str, Optional[int]]:
    return {k: _find_col(header, *needles) for k, needles in spec.items()}


def read_source(ws, spec, kind: str):
    """-> (mos qatorlar, o'tkazib yuborilganlar Counter, xom nom -> standart nom)."""
    values = ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
    header = values[0] if values else []
    c = cols_of(header, spec)
    matched, skipped, mapping, undated = [], Counter(), {}, []
    for i, row in enumerate(values[1:], start=2):
        raw = str(_cell(row, c["name"])).strip()
        if not raw:
            continue
        name = canonical(raw)
        if name is None:
            skipped[raw] += 1
            continue
        day = to_day(_cell(row, c["date"]))
        if day is None:
            undated.append((i, raw))
            continue
        if norm(raw) != norm(name):
            mapping[raw] = name
        rec = {"day": day, "name": name, "src_row": i, "qty": num(_cell(row, c["qty"]))}
        if kind == "in":
            src_unit = str(_cell(row, c["unit"])).strip()
            # Приход dagi birlik — qanday kelgan bo'lsa shunday (karobka, баллон ...); bo'sh bo'lsa katalogdagi
            rec.update(unit=src_unit or UNIT_OF[name], src_unit=src_unit,
                       price=num(_cell(row, c["price"])), note=str(_cell(row, c["note"])).strip())
        else:
            rec.update(purpose=str(_cell(row, c["purpose"])).strip(), who=str(_cell(row, c["who"])).strip())
        matched.append(rec)
    return matched, skipped, mapping, undated


def dedupe_key(rec: Dict[str, Any], kind: str) -> tuple:
    base = (rec["day"], norm(rec["name"]), str(rec["qty"]))
    return base + ((str(rec.get("price")),) if kind == "in" else (norm(rec.get("who", "")),))


def read_target(ws, spec, kind: str):
    """Maqsad varaqdagi mavjud qatorlar: kiritiladigan ustunlar FORMULA ko'rinishida (o'zgartirmasdan qayta
    yozish uchun)."""
    values = ws.get_all_values(value_render_option=ValueRenderOption.formula)
    header = values[0]
    c = cols_of(header, spec)
    rows = []
    for i, row in enumerate(values[1:], start=2):
        name = str(_cell(row, c["name"])).strip()
        if not name:
            continue
        rec = {"day": to_day(_cell(row, c["date"])), "raw_date": _cell(row, c["date"]), "name": name,
               "qty": _cell(row, c["qty"]), "row": i}
        if kind == "in":
            rec.update(unit=_cell(row, c["unit"]), price=_cell(row, c["price"]), note=_cell(row, c["note"]))
        else:
            rec.update(purpose=_cell(row, c["purpose"]), who=_cell(row, c["who"]))
        rows.append(rec)
    return header, c, rows, values


def plan_sheet(existing, imported, kind):
    keys = {dedupe_key(dict(r, qty=num(r["qty"]), price=num(r.get("price"))), kind) for r in existing if r["day"]}
    new, dup = [], []
    for r in imported:
        (dup if dedupe_key(r, kind) in keys else new).append(r)
    far = date.max
    merged = [(r["day"], 0, k, r) for k, r in enumerate(new)]
    merged += [(r["day"] or far, 1, k, dict(r, existing=True)) for k, r in enumerate(existing)]
    merged.sort(key=lambda x: (x[0], x[1], x[2]))
    return new, dup, [m[3] for m in merged]


def apply_sheet(sc: SheetsClient, ws, header, c, existing, new, merged, kind) -> List[Tuple[int, Dict[str, Any]]]:
    """Yangi qatorlarni 2-qatordan boshlab qo'shadi va butun ma'lumot blokini sana bo'yicha qayta yozadi."""
    n_new, total = len(new), len(merged)
    if not n_new:
        return []
    ss = sc.spreadsheet
    width = len(header)
    formula_col = c["total"] if kind == "in" else c["sum"]
    tables = [t for t in ss.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId),tables(tableId,range))"})["sheets"]
        if t["properties"]["sheetId"] == ws.id][0].get("tables", [])
    reqs: List[Dict[str, Any]] = [{"insertDimension": {
        "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": 1, "endIndex": 1 + n_new},
        "inheritFromBefore": False}}]
    ss.batch_update({"requests": reqs})
    # jadval (Table1 / Table2) chegarasi: sarlavha + barcha qatorlar
    tables_now = [t for t in ss.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId),tables(tableId,range))"})["sheets"]
        if t["properties"]["sheetId"] == ws.id][0].get("tables", [])
    reqs = []
    for tb in tables_now:
        rng = dict(tb["range"], sheetId=ws.id)
        if rng.get("startRowIndex", 0) == 0 and rng.get("endRowIndex", 0) < 1 + total:
            rng["endRowIndex"] = 1 + total
            reqs.append({"updateTable": {"table": {"tableId": tb["tableId"], "range": rng}, "fields": "range"}})
    if reqs:
        ss.batch_update({"requests": reqs})

    # kiritiladigan ustunlar qiymatlari (formula ustuniga tegilmaydi)
    data, placed = [], []
    for k, r in enumerate(merged):
        row = 2 + k
        placed.append((row, r))
        old = r.get("existing")
        vals: Dict[str, Any] = {
            "date": (r["raw_date"] if not r["day"] else r["day"].isoformat()) if old else r["day"].isoformat(),
            "name": r["name"], "qty": r["qty"]}
        if kind == "in":
            vals.update(unit=r["unit"], price=r["price"], note=r["note"])
        else:
            vals.update(purpose=r["purpose"], who=r["who"])
        for key, v in vals.items():
            col = c.get(key)
            if col is None:
                continue
            data.append({"range": rowcol_to_a1(row, col + 1), "values": [[v]]})
    ws.batch_update(data, value_input_option=ValueInputOption.user_entered)

    # namuna qator: eski 2-qator endi (2 + n_new) da. Format, ochiladigan ro'yxat va formula yangi qatorlarga
    tpl0 = 1 + n_new                                       # 0 dan boshlangan indeks
    reqs = []
    for paste in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
        reqs.append({"copyPaste": {
            "source": {"sheetId": ws.id, "startRowIndex": tpl0, "endRowIndex": tpl0 + 1,
                       "startColumnIndex": 0, "endColumnIndex": width},
            "destination": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 1 + n_new,
                            "startColumnIndex": 0, "endColumnIndex": width}, "pasteType": paste}})
    if formula_col is not None:
        for start, end in ((1, tpl0), (tpl0 + 1, 1 + total)):
            if end > start:
                reqs.append({"copyPaste": {
                    "source": {"sheetId": ws.id, "startRowIndex": tpl0, "endRowIndex": tpl0 + 1,
                               "startColumnIndex": formula_col, "endColumnIndex": formula_col + 1},
                    "destination": {"sheetId": ws.id, "startRowIndex": start, "endRowIndex": end,
                                    "startColumnIndex": formula_col, "endColumnIndex": formula_col + 1},
                    "pasteType": "PASTE_FORMULA"}})
    if kind == "in":                                       # narxsiz qatorlar sariq (bot kabi), qolganlari — fonsiz
        for row, r in placed:
            yellow = not parse_number(r.get("price"))
            cell = {"userEnteredFormat": {"backgroundColor": YELLOW}} if yellow else {}
            reqs.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": row - 1, "endRowIndex": row,
                          "startColumnIndex": 0, "endColumnIndex": width},
                "cell": cell, "fields": "userEnteredFormat.backgroundColor"}})
    ss.batch_update({"requests": reqs})
    del tables
    return placed


def append_directory_products(wh: WarehouseService, names: Sequence[str]) -> int:
    """Справочник «Наименование товара» ustuni oxiriga bir yo'la (Table4 ichida)."""
    ws = wh._ws(wh.directory_tab)
    values = ws.get_all_values()
    h = wh._dir_header_index(values)
    col = wh._dir_kind_col(values[h], PRODUCT)
    if col is None:
        return 0
    have = {norm(_cell(r, col)) for r in values[h + 1:] if str(_cell(r, col)).strip()}
    todo = [n for n in names if norm(n) not in have]
    if not todo:
        return 0
    last = h
    for i, row in enumerate(values[h + 1:], start=h + 1):
        if str(_cell(row, col)).strip():
            last = i
    first = last + 2
    end = first + len(todo) - 1
    if ws.row_count < end:
        ws.add_rows(end - ws.row_count + 50)
    ws.update([[n] for n in todo], f"{rowcol_to_a1(first, col + 1)}:{rowcol_to_a1(end, col + 1)}",
              value_input_option=ValueInputOption.raw)
    wh._expand_table(ws, h, end)
    return len(todo)


async def register_unpriced(settings, placed_in: List[Tuple[int, Dict[str, Any]]]) -> int:
    from app.storage.database import Database
    from app.storage.tracking import UnpricedStore
    db = Database(settings.db_path)
    try:
        tables = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name='unpriced_items'")
        if not tables:
            print("  (unpriced_items jadvali yo'q — o'tkazib yuborildi)")
            return 0
        store = UnpricedStore(db)
        n = 0
        for row, r in placed_in:
            if r.get("existing") or parse_number(r.get("price")):
                continue
            qty = parse_number(r["qty"]) or 0.0
            if qty <= 0:
                continue
            await store.add(row, r["name"], qty, r["unit"], r["day"].isoformat())
            n += 1
        return n
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="haqiqiy yozish (aks holda faqat hisobot)")
    ap.add_argument("--force", action="store_true", help="belgi qo'yilgan bo'lsa ham qayta ishga tushirish")
    args = ap.parse_args()

    settings = load_settings()
    sc = SheetsClient(settings)
    ss = sc.spreadsheet
    wh = WarehouseService(sc, settings.worksheet_name, settings.inflow_tab, settings.outflow_tab,
                          settings.directory_tab, settings.timezone)
    meta = ss.fetch_sheet_metadata(params={"fields": "developerMetadata(metadataKey,metadataValue)"})
    done = [m for m in meta.get("developerMetadata", []) or [] if m.get("metadataKey") == MARKER]
    if done and args.apply and not args.force:
        print(f"⛔ Migratsiya allaqachon bajarilgan ({done[0].get('metadataValue')}). Qayta yozilmaydi.")
        return

    src = gspread.service_account(filename=str(settings.credentials_file)).open_by_key(SOURCE_ID)
    src_in, skip_in, map_in, undated_in = read_source(src.worksheet("Приход"), IN_COLS, "in")
    src_out, skip_out, map_out, undated_out = read_source(src.worksheet("Расход"), OUT_COLS, "out")

    ws_in, ws_out = ss.worksheet(settings.inflow_tab), ss.worksheet(settings.outflow_tab)
    h_in, c_in, ex_in, raw_in = read_target(ws_in, IN_COLS, "in")
    h_out, c_out, ex_out, raw_out = read_target(ws_out, OUT_COLS, "out")
    new_in, dup_in, merged_in = plan_sheet(ex_in, src_in, "in")
    new_out, dup_out, merged_out = plan_sheet(ex_out, src_out, "out")

    catalog = wh._read_catalog()
    have = {norm(p.name): p for p in catalog}
    missing = [(n, c, u, 1.0, m) for n, u, c, m in CATALOG if norm(n) not in have]
    mismatch = []
    for n, u, c, m in CATALOG:
        p = have.get(norm(n))
        if p and (norm(p.unit) != norm(u) or norm(p.category) != norm(c) or float(p.min_stock) != float(m)):
            mismatch.append((n, (p.unit, p.category, p.min_stock), (u, c, m)))
    unit_diff = Counter((r["name"], r["unit"], UNIT_OF[r["name"]]) for r in new_in
                        if norm(r["unit"]) != norm(UNIT_OF[r["name"]]))

    mode = "YOZISH" if args.apply else "QURUQ YURISH (hech narsa yozilmaydi)"
    print(f"=== {mode} ===")
    print(f"Katalog: 63 tadan {63 - len(missing)} tasi bor, {len(missing)} tasi qo'shiladi")
    for n, old, new in mismatch:
        print(f"  ℹ️ mavjud mahsulot o'zgartirilmadi: {n}: varaqda {old}, ro'yxatda {new}")
    print(f"Приход: manbada mos {len(src_in)}, takror (allaqachon bor) {len(dup_in)}, import {len(new_in)}; "
          f"maqsadda mavjud {len(ex_in)}")
    print(f"Расход: manbada mos {len(src_out)}, takror (allaqachon bor) {len(dup_out)}, import {len(new_out)}; "
          f"maqsadda mavjud {len(ex_out)}")
    if dup_in or dup_out:
        for r in dup_in + dup_out:
            print(f"  ↺ takror o'tkazildi: {r['day']} {r['name']} {r['qty']} (manba qatori {r['src_row']})")
    print("Nom moslashtirish (manba -> standart):")
    for raw, name in sorted({**map_in, **map_out}.items()):
        print(f"  {raw!r} -> {name}")
    if unit_diff:
        print("ℹ️ Приход birligi katalogdan farq qiladi (asl holicha saqlanadi: Приход -> katalog):")
        for (n, a, b), k in sorted(unit_diff.items()):
            print(f"  {n}: {a!r} -> {b!r} ({k} qator)")
    print("O'tkazib yuborildi (63 ta katalogda yo'q):")
    for label, sk in (("Приход", skip_in), ("Расход", skip_out)):
        print(f"  {label}: {sum(sk.values())} qator, {len(sk)} nom")
        for raw, k in sorted(sk.items(), key=lambda x: x[0].casefold()):
            print(f"    - {raw} ×{k}")
    for label, und in (("Приход", undated_in), ("Расход", undated_out)):
        for i, raw in und:
            print(f"  ⚠️ {label}: sanasiz qator o'tkazildi (manba {i}): {raw}")
    unpriced_new = sum(1 for r in new_in if not parse_number(r.get("price")))
    print(f"Narxsiz (0 / bo'sh) import qilinadigan Приход qatorlari: {unpriced_new}")
    if new_in:
        print(f"Sana oralig'i: Приход {min(r['day'] for r in new_in)} … {max(r['day'] for r in new_in)}")
    if new_out:
        print(f"              Расход {min(r['day'] for r in new_out)} … {max(r['day'] for r in new_out)}")
    if not args.apply:
        return

    # ---- zaxira nusxa ----
    backup_dir = BASE / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(settings.timezone).strftime("%Y%m%d_%H%M%S")
    backup = {tab: ss.worksheet(tab).get_all_values(value_render_option=ValueRenderOption.formula)
              for tab in (settings.inflow_tab, settings.outflow_tab, settings.worksheet_name, settings.directory_tab)}
    path = backup_dir / f"before_history_import_{stamp}.json"
    path.write_text(json.dumps(backup, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"💾 Zaxira nusxa: data/backups/{path.name}")

    # ---- 1) katalog ----
    added = 0
    if missing:
        values = wh._ws(wh.inventory_tab).get_all_values()
        wh._add_products_rows(values[0], missing)
        added = len(missing)
    for u in sorted({u for _n, u, _c, _m in CATALOG}):
        wh._dir_append_sync(UNIT_KIND, u)
    dir_added = append_directory_products(wh, [n for n, *_ in CATALOG])
    print(f"✅ Остатки и Расходы: +{added} mahsulot; Справочник: +{dir_added} nom")

    # ---- 2) tarix ----
    placed_in = apply_sheet(sc, ws_in, h_in, c_in, ex_in, new_in, merged_in, "in")
    placed_out = apply_sheet(sc, ws_out, h_out, c_out, ex_out, new_out, merged_out, "out")
    print(f"✅ Приход: +{len(new_in)} qator; Расход: +{len(new_out)} qator (sana bo'yicha, bugungilar pastda)")

    # ---- 3) narxsiz kirimlar nazorati ----
    n_unpriced = asyncio.run(register_unpriced(settings, placed_in))
    print(f"✅ unpriced_items: +{n_unpriced}")

    ss.batch_update({"requests": [{"createDeveloperMetadata": {"developerMetadata": {
        "metadataKey": MARKER, "metadataValue": stamp, "location": {"spreadsheet": True},
        "visibility": "DOCUMENT"}}}]})
    print("🏁 Tayyor.")


if __name__ == "__main__":
    main()
