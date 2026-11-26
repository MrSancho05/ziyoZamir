"""📥 Kirim / 📤 Chiqim ("savat" usulida) va 📦 Mahsulotlar katalogi. FAQAT admin va sklad nazoratchisi.

Kirim:  mahsulot -> (karobka/dona) -> miqdor -> narx -> do'kon -> savat
Chiqim: mahsulot (qoldiq ko'rsatiladi) -> miqdor -> oluvchi -> buyurtma/sex -> savat
Savat:  ➕ Yana qo'shish | 🗑 O'chirish | ✅ Tasdiqlash | ❌ Bekor qilish  -> bitta so'rov bilan jadvalga yoziladi.
Narx:   haqiqiy narx (> 0), 1 bosishda "💰 Oxirgi narx" yoki "⏳ Narxi keyin kiritiladi" (jadvalda sariq qator,
        keyin 📦 Mahsulotlar -> 💰 Narxi yo'q tovarlar orqali 1 bosishda to'ldiriladi).
Kategoriyalar: umumiy ro'yxat (🏷 Kategoriyalar), yangi mahsulotga avtomatik tavsiya (🤖).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.config import Settings
from app.core.permissions import Permission, Role
from app.filters.permission import HasPermission
from app.keyboards import warehouse as kb
from app.keyboards.callbacks import WhCb
from app.locales.texts import all_texts, get_texts, menu_texts
from app.models import User
from app.services import kromka
from app.services.flows import FlowsService
from app.services.categories import examples as cat_examples
from app.services.categories import label as cat_label
from app.services.categories import suggest as suggest_category
from app.services.search import exact_match, normalize, search_products
from app.services.warehouse import (
    ADJ_IN_NOTE,
    ADJ_OUT_NOTE,
    MASTER,
    RECIPIENT,
    UNIT_KIND,
    SUPPLIER,
    UNITS,
    DuplicateError,
    InflowItem,
    LastProductError,
    NotFoundError,
    OutflowItem,
    Product,
    WarehouseService,
    clean_text,
    parse_number,
)
from app.storage.memory import UserStore
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)
router = Router(name="warehouse")

# Faqat admin va sklad nazoratchisi (boss va ishchida tugmalar ham ko'rinmaydi)
router.message.filter(HasPermission(Permission.MANAGE_WAREHOUSE))
router.callback_query.filter(HasPermission(Permission.MANAGE_WAREHOUSE))

PAGE_SIZE = 10
FLOW_IN, FLOW_OUT, FLOW_REN, FLOW_DEL = "in", "out", "ren", "del"


class WhSt(StatesGroup):
    search = State()
    mode = State()
    qty = State()
    price = State()
    who = State()
    purpose = State()
    np_name = State()
    np_cat = State()
    np_unit = State()
    np_pack = State()
    np_min = State()
    np_confirm = State()
    rn_name = State()
    confirm = State()
    unp_price = State()      # 💰 narxi yo'q tovar: narx kutilmoqda
    cat_name = State()       # 🏷 yangi kategoriya nomi
    cat_rn = State()         # 🏷 kategoriyaning yangi nomi
    dir_add = State()        # 👷 usta / 📏 o'lchov birligi: yangi qiymat kutilmoqda
    ed_card = State()        # ✏️ mahsulot ma'lumotlari kartasi
    ed_unit = State()        # ✏️ yangi birlik tanlanmoqda
    ed_cat = State()         # ✏️ yangi kategoriya tanlanmoqda
    ed_min = State()         # ✏️ yangi Мин. норматив kutilmoqda
    over = State()           # 📤 qoldiqdan ko'p chiqim: tanlov kutilmoqda
    seq_price = State()      # 💰 narxlarni birma-bir kiritish
    kir_pack = State()       # 📥 Kirim davomida karobkadagi miqdorni o'zgartirish
    ed_pack = State()        # ✏️ karobkadagi miqdor (В упаковке)
    ed_price = State()       # ✏️ 💰 narxni belgilash / o'zgartirish
    ed_adj = State()         # ⚖️ qoldiqni to'g'rilash: +N / -N kutilmoqda
    krm_size = State()       # 🎞 Кромка ПВХ: o'lcham tanlanmoqda
    krm_color = State()      # 🎞 Кромка ПВХ: mavjud rang (faqat tugma)
    krm_new = State()        # 🎞 ➕ yangi kromka: rang nomi kutilmoqda


# ---------------- yordamchilar ----------------
def _n(x: Optional[float]) -> str:
    """12500 -> '12 500', 2.5 -> '2.5'."""
    if x is None:
        return "—"
    s = f"{round(float(x), 2):,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


def _lang(t: Dict[str, str]) -> str:
    return t.get("lang_code", "uz")


def _price_text(t: Dict[str, str], price: Optional[float]) -> str:
    return _n(price) if price else t["wh_price_later_mark"]


async def _show(target: Message, text: str, markup: Optional[InlineKeyboardMarkup], edit: bool) -> None:
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    await target.answer(text, reply_markup=markup)


def _title(t: Dict[str, str], flow: str) -> str:
    return {FLOW_IN: t["wh_title_in"], FLOW_OUT: t["wh_title_out"],
            FLOW_REN: t["wh_pm_pick_rename"], FLOW_DEL: t["wh_pm_pick_delete"]}.get(flow, "")


def _who_kind(flow: str) -> str:
    return SUPPLIER if flow == FLOW_IN else RECIPIENT


def _cart_text(t: Dict[str, str], flow: str, cart: List[Dict[str, Any]]) -> str:
    if not cart:
        return t["wh_cart_empty"]
    lines = [_title(t, flow), "", t["wh_cart_title"].format(count=len(cart))]
    total = 0.0
    used: Dict[str, float] = {}
    for i, it in enumerate(cart, start=1):
        name, unit = escape(it["name"]), escape(it["unit"])
        if flow == FLOW_IN:
            box = f" ({_n(it['boxes'])} {t['wh_unit_box']})" if it.get("boxes") else ""
            if it.get("price"):
                amount = it["qty"] * it["price"]
                total += amount
                cost = f" × {_n(it['price'])} = <b>{_n(amount)}</b>"
            else:
                cost = f" · {t['wh_price_later_mark']}"
            lines.append(f"{i}. <b>{name}</b> — {_n(it['qty'])} {unit}{box}{cost}\n    🏪 {escape(it['who'])}")
        else:
            key = it["name"].casefold()
            used[key] = used.get(key, 0.0) + it["qty"]
            warn = ""
            if it.get("stock") is not None and used[key] > it["stock"]:
                warn = f" {t['wh_over_stock']}"
            purpose = f" · 🛠 {escape(it['purpose'])}" if it.get("purpose") else ""
            lines.append(f"{i}. <b>{name}</b> — {_n(it['qty'])} {unit}{warn}\n    👷 {escape(it['who'])}{purpose}")
    if flow == FLOW_IN:
        lines += ["", t["wh_cart_total"].format(total=_n(total))]
        later = sum(1 for it in cart if not it.get("price"))
        if later:
            lines.append(t["wh_cart_unpriced"].format(count=later))
    return "\n".join(lines)


async def _show_cart(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    cart = data.get("cart", [])
    # savat ko'rinib turganda keyingi mahsulot nomini darhol yozish mumkin
    await state.set_state(WhSt.search)
    await state.update_data(view="cart", cur=None)
    await _show(target, _cart_text(t, data.get("flow", FLOW_IN), cart), kb.cart_kb(t, bool(cart)), edit)


async def _show_categories(target: Message, t: Dict[str, str], state: FSMContext,
                           warehouse: WarehouseService, edit: bool) -> None:
    data = await state.get_data()
    try:
        cats = await warehouse.all_categories()
    except Exception:
        logger.exception("Kategoriyalarni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    await state.set_state(WhSt.search)
    await state.update_data(cats=cats, view="pick")
    text = f"{_title(t, data.get('flow', FLOW_IN))}\n\n{t['wh_pick'] if cats else t['wh_no_cats']}"
    labels = [cat_label(c, _lang(t)) for c in cats]
    flow = data.get("flow", FLOW_IN)
    await _show(target, text, kb.categories_kb(t, labels, len(data.get("cart", [])),
                                               kromka=flow in (FLOW_IN, FLOW_OUT)), edit)


async def _show_products_page(target: Message, t: Dict[str, str], state: FSMContext, page: int) -> None:
    data = await state.get_data()
    opts: List[str] = data.get("opts", [])
    pages = max(1, math.ceil(len(opts) / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chunk = opts[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    cats = data.get("cats", [])
    cat = cats[data.get("cat", 0)] if cats and data.get("cat", 0) < len(cats) else ""
    text = t["wh_pick_cat"].format(cat=escape(cat_label(cat, _lang(t))), page=page + 1, pages=pages)
    await _show(target, text, kb.products_kb(t, chunk, page, pages, offset=page * PAGE_SIZE), True)


# ---------------- menyu tugmasi bosilganda: ochiq jarayon to'xtatiladi ----------------
@router.message(StateFilter(WhSt), F.text.in_(menu_texts()) | F.text.startswith("/"))
async def leave_on_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    raise SkipHandler()


# ---------------- kirish nuqtalari ----------------
async def _open_flow(message: Message, t: Dict[str, str], user: User, state: FSMContext, store: UserStore,
                     warehouse: WarehouseService, flow: str) -> None:
    await state.clear()
    store.clear_awaiting(user.id)          # do'kon xarajati summasi kutilayotgan bo'lsa bekor qilinadi
    await state.update_data(flow=flow, cart=[])
    await _show_categories(message, t, state, warehouse, edit=False)


@router.message(F.text.in_(all_texts("btn_inflow")))
async def open_inflow(message: Message, t: Dict[str, str], user: User, state: FSMContext, store: UserStore,
                      warehouse: WarehouseService) -> None:
    await _open_flow(message, t, user, state, store, warehouse, FLOW_IN)


@router.message(F.text.in_(all_texts("btn_outflow")))
async def open_outflow(message: Message, t: Dict[str, str], user: User, state: FSMContext, store: UserStore,
                       warehouse: WarehouseService) -> None:
    await _open_flow(message, t, user, state, store, warehouse, FLOW_OUT)


@router.message(F.text.in_(all_texts("btn_products")))
async def open_products(message: Message, t: Dict[str, str], user: User, state: FSMContext,
                        store: UserStore) -> None:
    await state.clear()
    store.clear_awaiting(user.id)
    await message.answer(t["wh_pm_title"], reply_markup=kb.pm_menu_kb(t))


@router.callback_query(WhCb.filter(F.a == "noop"))
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(WhCb.filter(F.a == "pm"))
async def pm_action(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                    warehouse: WarehouseService) -> None:
    await state.clear()
    if callback_data.k == "add":
        await state.update_data(np={"ret": None}, cart=[])
        await state.set_state(WhSt.np_name)
        await _show(callback.message, t["wh_np_name"], kb.cancel_kb(t), True)
    else:
        await state.update_data(flow=FLOW_REN if callback_data.k == "ren" else FLOW_DEL, cart=[])
        await _show_categories(callback.message, t, state, warehouse, edit=True)
    await callback.answer()


# ---------------- mahsulot tanlash: kategoriya / sahifa / qidiruv ----------------
@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "cats"))
async def back_to_categories(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                             warehouse: WarehouseService) -> None:
    await _show_categories(callback.message, t, state, warehouse, edit=True)
    await callback.answer()


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "more"))
async def add_more(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                   warehouse: WarehouseService) -> None:
    await _show_categories(callback.message, t, state, warehouse, edit=True)
    await callback.answer()


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "cat"))
async def pick_category(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    data = await state.get_data()
    cats = data.get("cats", [])
    if callback_data.i >= len(cats):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    if cats[callback_data.i] == kromka.CATEGORY and data.get("flow") in (FLOW_IN, FLOW_OUT):
        await callback.answer()
        await _krm_sizes(callback.message, t, state, edit=True)
        return
    try:
        names = [p.name for p in await warehouse.products_in(cats[callback_data.i])]
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await callback.answer(t["error"], show_alert=True)
        return
    await state.update_data(opts=names, cat=callback_data.i, view="pick")
    await callback.answer()
    if not names:                     # yangi (bo'sh) kategoriya: mahsulot qo'shish taklif qilinadi
        flow = (await state.get_data()).get("flow", FLOW_IN)
        await state.update_data(q="", np_cat_hint=cats[callback_data.i])
        await _show(callback.message, t["wh_cat_empty"].format(cat=escape(cat_label(cats[callback_data.i], _lang(t)))),
                    kb.search_kb(t, [], flow in (FLOW_IN, FLOW_OUT)), True)
        return
    await _show_products_page(callback.message, t, state, 0)


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "pg"))
async def products_page(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                        state: FSMContext) -> None:
    await _show_products_page(callback.message, t, state, callback_data.i)
    await callback.answer()


@router.message(WhSt.search, F.text)
async def search_text(message: Message, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    query = clean_text(message.text, 60)
    if not query:
        return
    data = await state.get_data()
    flow = data.get("flow", FLOW_IN)
    if len(query.replace(" ", "")) < 2:                     # 1 harf — qidirib bo'lmaydi: qanday qidirishni tushuntiramiz
        await message.answer(t["wh_search_short"], reply_markup=kb.short_search_kb(t))
        return
    if flow in (FLOW_IN, FLOW_OUT) and kromka.is_kromka(query):
        # kromka faqat o'lcham -> mavjud rang tugmalari orqali (erkin nom bilan kiritilmaydi)
        await state.update_data(q=query, view="pick", np_cat_hint=None)
        await _krm_route(message, t, state, warehouse, query, edit=False)
        return
    try:
        names = [p.name for p in await warehouse.catalog()]
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await message.answer(t["error"])
        return
    await state.update_data(q=query, view="pick", np_cat_hint=None)

    exact = exact_match(query, names)
    if exact:
        await state.update_data(opts=[exact])
        await _pick(message, t, state, warehouse, exact, edit=False)
        return

    q = normalize(query).replace(" ", "")
    hits = [n for n in names if q and q in normalize(n).replace(" ", "")]
    hits.sort(key=lambda n: (len(n), n.casefold()))
    allow_new = flow in (FLOW_IN, FLOW_OUT)
    if hits:
        hits = hits[:8]
        text = t["wh_results"].format(query=escape(query))
    else:
        hits = search_products(query, names, limit=3)
        if not hits and allow_new:
            # hech narsa topilmadi: shu nom bilan darhol yangi mahsulot qo'shishni taklif qilamiz
            await state.update_data(opts=[])
            await message.answer(t["wh_nf_add"].format(query=escape(query)), reply_markup=kb.not_found_kb(t, query))
            return
        text = (t["wh_did_you_mean"] if hits else t["wh_not_found"]).format(query=escape(query))
    await state.update_data(opts=hits)
    await message.answer(text, reply_markup=kb.search_kb(t, hits, allow_new, query))


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "p"))
async def pick_product(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                       warehouse: WarehouseService) -> None:
    opts = (await state.get_data()).get("opts", [])
    if callback_data.i >= len(opts):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _pick(callback.message, t, state, warehouse, opts[callback_data.i], edit=True)


async def _pick(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                name: str, edit: bool) -> None:
    try:
        product = await warehouse.product(name)
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    if product is None:
        await target.answer(t["wh_not_in_catalog"])
        return
    data = await state.get_data()
    flow = data.get("flow", FLOW_IN)
    if flow == FLOW_REN:
        await _show_edit_card(target, t, state, warehouse, product.name, edit)
    elif flow == FLOW_DEL:
        await state.update_data(act={"kind": "del", "name": product.name}, view="item")
        await state.set_state(WhSt.confirm)
        await _show(target, t["wh_del_confirm2"].format(name=escape(product.name)),
                    kb.confirm_kb(t, t["wh_btn_yes_delete"]), edit)
    else:
        await _start_item(target, t, state, product, edit)


async def _start_item(target: Message, t: Dict[str, str], state: FSMContext, product: Product,
                      edit: bool) -> None:
    data = await state.get_data()
    flow = data.get("flow", FLOW_IN)
    cur = {"name": product.name, "unit": product.unit, "pack": product.pack, "stock": product.stock,
           "mode": "unit"}
    await state.update_data(cur=cur, view="item")
    if flow == FLOW_OUT and _available(data, cur) <= 0:
        await state.set_state(WhSt.search)
        await state.update_data(view="pick")
        await _show(target, t["wh_out_zero"].format(name=escape(product.name), unit=escape(product.unit),
                                                   stock=_n(max(_available(data, cur), 0))),
                    kb.zero_stock_kb(t), edit)
        return
    if flow == FLOW_IN and product.pack > 1:
        await _show_mode(target, t, state, edit)
        return
    await _ask_qty(target, t, state, edit)


async def _show_mode(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    cur = (await state.get_data())["cur"]
    await state.set_state(WhSt.mode)
    await _show(target, t["wh_mode_prompt"].format(name=escape(cur["name"]), pack=_n(cur["pack"]),
                                                   unit=escape(cur["unit"])),
                kb.mode_kb(t, _n(cur["pack"]), cur["unit"]), edit)


BOX_UNITS = ("упак", "уп", "кор", "коробка", "каробка", "пачка", "karobka", "quti", "pack", "box")


def _qty_tip(t: Dict[str, str], cur: Dict[str, Any]) -> str:
    """Karobka / dona chalkashligining oldini olish uchun aniq misol."""
    unit = str(cur.get("unit") or "").strip().lower().rstrip(".")
    pack = float(cur.get("pack") or 1)
    if cur.get("mode") == "pack":
        return t["wh_qty_tip_pack_mode"].format(pack=_n(pack), unit=escape(cur["unit"]), total=_n(2 * pack))
    if unit in BOX_UNITS:
        return t["wh_qty_tip_box"]
    if unit in ("шт", "dona", "штук"):
        example = pack if pack > 1 else 200
        return t["wh_qty_tip_pcs"].format(pack=_n(example), total=_n(2 * example))
    return t["wh_qty_tip_other"].format(unit=escape(cur["unit"]))


async def _ask_qty(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    cur = data["cur"]
    unit = t["wh_unit_box"] if cur.get("mode") == "pack" else cur["unit"]
    await state.set_state(WhSt.qty)
    # savatda shu mahsulotdan oldin olingani ham hisobga olinadi
    taken = sum(it["qty"] for it in data.get("cart", []) if it["name"] == cur["name"]) \
        if data.get("flow") == FLOW_OUT else 0.0
    key = "wh_qty_prompt_out" if data.get("flow") == FLOW_OUT else "wh_qty_prompt_in"
    text = t[key].format(name=escape(cur["name"]), stock=_n(cur["stock"] - taken), unit=escape(unit),
                         base_unit=escape(cur["unit"]), tip=_qty_tip(t, cur))
    if data.get("flow") == FLOW_IN:
        text += "\n" + t["wh_pack_now"].format(pack=_n(cur.get("pack") or 1), unit=escape(cur["unit"]))
        await _show(target, text, kb.qty_in_kb(t, _n(cur.get("pack") or 1)), edit)
        return
    await _show(target, text, kb.cancel_kb(t), edit)


@router.callback_query(WhSt.mode, WhCb.filter(F.a == "mode"))
async def choose_mode(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                      state: FSMContext) -> None:
    data = await state.get_data()
    cur = dict(data.get("cur") or {})
    if not cur:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    cur["mode"] = "pack" if callback_data.k == "pack" else "unit"
    await state.update_data(cur=cur)
    await _ask_qty(callback.message, t, state, edit=True)
    await callback.answer()


@router.message(WhSt.qty, F.text)
async def enter_qty(message: Message, t: Dict[str, str], state: FSMContext,
                    warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    data = await state.get_data()
    cur = dict(data["cur"])
    error = _number_error(t, message.text, value, "qty", unit=cur.get("unit", ""))
    if error:
        await message.answer(error, reply_markup=kb.cancel_kb(t))
        return
    cur["qty_in"] = value
    await state.update_data(cur=cur)
    if data.get("flow") == FLOW_IN:
        await _ask_price(message, t, state, warehouse, edit=False)
        return
    avail = _available(data, cur)
    if value > avail + 1e-9:
        # omborda bunchalik yo'q: "borini chiqim qilish" yoki "boshqa son"
        await state.set_state(WhSt.over)
        if avail <= 0:
            await message.answer(t["wh_out_zero"].format(name=escape(cur["name"]), unit=escape(cur["unit"]),
                                                         stock=_n(0)), reply_markup=kb.zero_stock_kb(t))
            return
        await message.answer(t["wh_out_over"].format(avail=_n(avail), qty=_n(value), unit=escape(cur["unit"]),
                                                     name=escape(cur["name"])),
                             reply_markup=kb.over_stock_kb(t, _n(avail), cur["unit"]))
        return
    cur["qty"] = value
    await state.update_data(cur=cur)
    await _ask_who(message, t, state, warehouse, edit=False)


async def _ask_price(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                     edit: bool) -> None:
    cur = dict((await state.get_data())["cur"])
    try:
        last = await warehouse.last_price(cur["name"])
    except Exception:
        logger.exception("Oxirgi narxni o'qib bo'lmadi")
        last = None
    if last and cur.get("mode") == "pack":
        last = last * cur["pack"]
    cur["last_price"] = last
    await state.update_data(cur=cur)
    await state.set_state(WhSt.price)
    unit = t["wh_unit_box"] if cur.get("mode") == "pack" else cur["unit"]
    await _show(target, t["wh_price_prompt"].format(unit=escape(unit)),
                kb.price_kb(t, _n(last) if last else None), edit)


async def _set_price(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                     price_in: Optional[float], edit: bool) -> None:
    """price_in=None: "⏳ Narxi keyin kiritiladi" (jadvalda narx bo'sh, qator sariq)."""
    data = await state.get_data()
    cur = dict(data["cur"])
    if cur.get("mode") == "pack":
        # karobka -> asosiy birlik: Количество = karobka * pack, Цена за ед. = karobka narxi / pack
        cur["boxes"] = cur["qty_in"]
        cur["qty"] = cur["qty_in"] * cur["pack"]
        cur["price"] = price_in / cur["pack"] if price_in else None
    else:
        cur["qty"] = cur["qty_in"]
        cur["price"] = price_in or None
    await state.update_data(cur=cur)
    await _ask_who(target, t, state, warehouse, edit)


@router.message(WhSt.price, F.text)
async def enter_price(message: Message, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _number_error(t, message.text, value, "price")
    if error:
        await message.answer(error, reply_markup=kb.cancel_kb(t))
        return
    await _set_price(message, t, state, warehouse, value, edit=False)


@router.callback_query(WhSt.price, WhCb.filter(F.a == "pl"))
async def price_later(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    if not (await state.get_data()).get("cur"):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer(t["wh_price_later_toast"])
    await _set_price(callback.message, t, state, warehouse, None, edit=True)


@router.callback_query(WhSt.price, WhCb.filter(F.a == "lp"))
async def last_price(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                     warehouse: WarehouseService) -> None:
    cur = (await state.get_data()).get("cur") or {}
    if not cur.get("last_price"):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _set_price(callback.message, t, state, warehouse, float(cur["last_price"]), edit=True)


async def _ask_who(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                   edit: bool) -> None:
    data = await state.get_data()
    flow = data.get("flow", FLOW_IN)
    kind = _who_kind(flow)
    try:
        top = await warehouse.top_names(kind, limit=6)
    except Exception:
        logger.exception("Справочник ni o'qib bo'lmadi")
        top = []
    # shu savatda oxirgi tanlangan nom birinchi bo'lib turadi
    recent = [it["who"] for it in reversed(data.get("cart", [])) if it.get("who")]
    names: List[str] = []
    for n in recent + top:
        if n.casefold() not in {x.casefold() for x in names}:
            names.append(n)
    names = names[:6]
    await state.update_data(who_opts=names)
    await state.set_state(WhSt.who)
    prompt = t["wh_supplier_prompt"] if kind == SUPPLIER else t["wh_recipient_prompt"]
    new_label = t["wh_btn_new_supplier"] if kind == SUPPLIER else t["wh_btn_new_recipient"]
    await _show(target, prompt, kb.who_kb(t, names, new_label), edit)


@router.callback_query(WhSt.who, WhCb.filter(F.a == "whonew"))
async def who_new(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    flow = (await state.get_data()).get("flow", FLOW_IN)
    key = "wh_who_text_supplier" if _who_kind(flow) == SUPPLIER else "wh_who_text_recipient"
    await _show(callback.message, t[key], kb.cancel_kb(t), True)
    await callback.answer()


@router.callback_query(WhSt.who, WhCb.filter(F.a == "who"))
async def who_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                   warehouse: WarehouseService) -> None:
    names = (await state.get_data()).get("who_opts", [])
    if callback_data.i >= len(names):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _set_who(callback.message, t, state, warehouse, names[callback_data.i], edit=True)


@router.message(WhSt.who, F.text)
async def who_text(message: Message, t: Dict[str, str], state: FSMContext,
                   warehouse: WarehouseService) -> None:
    name = clean_text(message.text, 60)
    if not name:
        return
    await _set_who(message, t, state, warehouse, name, edit=False)


async def _set_who(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                   name: str, edit: bool) -> None:
    data = await state.get_data()
    flow = data.get("flow", FLOW_IN)
    cur = dict(data["cur"])
    cur["who"] = name
    await state.update_data(cur=cur)
    await warehouse.remember_name(_who_kind(flow), name)      # yangi bo'lsa Справочник ga qo'shiladi
    if flow == FLOW_IN:
        await _add_to_cart(target, t, state, edit)
        return
    await _ask_purpose(target, t, state, edit)


async def _ask_purpose(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    recent: List[str] = []
    for it in reversed(data.get("cart", [])):
        p = it.get("purpose")
        if p and p not in recent:
            recent.append(p)
    await state.update_data(pur_opts=recent[:4])
    await state.set_state(WhSt.purpose)
    await _show(target, t["wh_purpose_prompt"], kb.purpose_kb(t, recent[:4]), edit)


@router.message(WhSt.purpose, F.text)
async def purpose_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    await _set_purpose(message, t, state, clean_text(message.text, 120), edit=False)


@router.callback_query(WhSt.purpose, WhCb.filter(F.a.in_({"skip", "pur"})))
async def purpose_button(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                         state: FSMContext) -> None:
    value = ""
    if callback_data.a == "pur":
        opts = (await state.get_data()).get("pur_opts", [])
        value = opts[callback_data.i] if callback_data.i < len(opts) else ""
    await callback.answer()
    await _set_purpose(callback.message, t, state, value, edit=True)


async def _set_purpose(target: Message, t: Dict[str, str], state: FSMContext, value: str, edit: bool) -> None:
    cur = dict((await state.get_data())["cur"])
    cur["purpose"] = value
    await state.update_data(cur=cur)
    await _add_to_cart(target, t, state, edit)


async def _add_to_cart(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    cur = data.get("cur")
    cart = list(data.get("cart", []))
    if cur:
        cart.append(cur)
    await state.update_data(cart=cart, cur=None)
    await _show_cart(target, t, state, edit)


# ---------------- savat ----------------
@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "cart"))
async def back_to_cart(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await _show_cart(callback.message, t, state, edit=True)
    await callback.answer()


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "rm"))
async def remove_menu(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    cart = (await state.get_data()).get("cart", [])
    if not cart:
        await callback.answer(t["wh_cart_empty"], show_alert=True)
        return
    await _show(callback.message, t["wh_remove_prompt"], kb.remove_kb(t, [it["name"] for it in cart]), True)
    await callback.answer()


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "dl"))
async def remove_item(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                      state: FSMContext) -> None:
    cart = list((await state.get_data()).get("cart", []))
    if callback_data.i < len(cart):
        cart.pop(callback_data.i)
    await state.update_data(cart=cart)
    await _show_cart(callback.message, t, state, edit=True)
    await callback.answer()


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "save"))
async def save_cart(callback: CallbackQuery, t: Dict[str, str], user: User, state: FSMContext,
                    warehouse: WarehouseService, flows: FlowsService, repo: UserRepository,
                    settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    cart = data.get("cart", [])
    flow = data.get("flow", FLOW_IN)
    if not cart or flow not in (FLOW_IN, FLOW_OUT):
        await callback.answer(t["wh_cart_empty"], show_alert=True)
        return
    if data.get("saving"):
        await callback.answer()
        return
    await state.update_data(saving=True)            # ikki marta bosilsa ikki marta yozilmasin
    await callback.answer("⏳")
    try:
        if flow == FLOW_IN:
            items = [InflowItem(name=it["name"], unit=it["unit"], qty=it["qty"], price=it["price"],
                                supplier=it["who"], boxes=it.get("boxes") or 0.0, pack=it["pack"])
                     for it in cart]
            rows = await warehouse.save_inflow(items)
            tab = warehouse.inflow_tab
            await _track_inflow(warehouse, items, rows)
        else:
            items = [OutflowItem(name=it["name"], unit=it["unit"], qty=it["qty"], purpose=it.get("purpose", ""),
                                 recipient=it["who"], stock_before=it.get("stock")) for it in cart]
            await warehouse.save_outflow(items)
            tab = warehouse.outflow_tab
    except Exception:
        logger.exception("Savatni jadvalga yozib bo'lmadi")
        await state.update_data(saving=False)
        await callback.message.answer(t["wh_save_error"])
        return

    summary = _cart_text(t, flow, cart)
    await state.clear()
    await _show(callback.message, f"{t['wh_saved'].format(count=len(cart), tab=escape(tab))}\n\n{summary}",
                None, True)

    # kunlik kirim/chiqim hisobi (flows_store) yangilanadi: shu kunning qatorlari qayta yoziladi
    try:
        await flows.commit(datetime.now(settings.timezone).date())
    except Exception:
        logger.exception("flows_store ni yangilab bo'lmadi")

    try:
        low = await warehouse.low_stock([it["name"] for it in cart])
    except Exception:
        logger.exception("Qoldiqni tekshirib bo'lmadi")
        low = []
    if low:
        await _send_low_alert(bot, repo, user, low)


async def _send_low_alert(bot: Bot, repo: UserRepository, user: User, low: List[Product]) -> None:
    recipients: Dict[int, User] = {user.id: user}
    for role in (Role.ADMIN, Role.STOREKEEPER):
        for u in await repo.list_by_role(role):
            recipients.setdefault(u.id, u)
    for u in recipients.values():
        tt = get_texts(u.lang)
        lines = [tt["wh_low_title"], ""] + [
            tt["wh_low_line"].format(name=escape(p.name), stock=_n(p.stock), unit=escape(p.unit),
                                     min=_n(p.min_stock)) for p in low]
        try:
            await bot.send_message(u.id, "\n".join(lines))
        except Exception:
            logger.warning("Kam qoldiq xabarini %s ga yuborib bo'lmadi", u.id)


# ---------------- bekor qilish ----------------
@router.callback_query(WhCb.filter(F.a == "cancel"))
async def cancel(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    data = await state.get_data()
    np = data.get("np") or {}
    ret = np.get("ret") if await state.get_state() in _NP_STATES else None
    if ret:
        # savatdan "Yangi mahsulot" ga o'tilgan edi: savatga qaytamiz
        await state.update_data(flow=ret, np=None)
    if data.get("cart") and data.get("view") != "cart":
        await _show_cart(callback.message, t, state, edit=True)
    else:
        await state.clear()
        await _show(callback.message, t["wh_cancelled"], None, True)
    await callback.answer()


# ---------------- 📦 yangi mahsulot ----------------
_NP_STATES = {WhSt.np_name.state, WhSt.np_cat.state, WhSt.np_unit.state, WhSt.np_pack.state,
              WhSt.np_min.state, WhSt.np_confirm.state}


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "new"))
async def new_from_search(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    data = await state.get_data()
    await state.update_data(np={"ret": data.get("flow")}, view="item")
    await callback.answer()
    query = data.get("q") or ""
    if query:
        await _np_set_name(callback.message, t, state, warehouse, query, edit=True)
    else:
        await state.set_state(WhSt.np_name)
        await _show(callback.message, t["wh_np_name"], kb.cancel_kb(t), True)


@router.message(WhSt.np_name, F.text)
async def np_name(message: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService) -> None:
    await _np_set_name(message, t, state, warehouse, clean_text(message.text, 100), edit=False)


async def _np_update(state: FSMContext, **fields: Any) -> Dict[str, Any]:
    np = dict((await state.get_data()).get("np") or {})
    np.update(fields)
    await state.update_data(np=np)
    return np


async def _np_set_name(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                       name: str, edit: bool) -> None:
    if not name:
        return
    np0 = (await state.get_data()).get("np") or {}
    if np0.get("ret") in (FLOW_IN, FLOW_OUT) and kromka.is_kromka(name) and not np0.get("krm"):
        await state.update_data(flow=np0["ret"], np=None)
        await _krm_route(target, t, state, warehouse, name, edit)
        return
    try:
        catalog = await warehouse.catalog(force=True)
        cats = await warehouse.all_categories()
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    dup = exact_match(name, [p.name for p in catalog])
    if dup:
        await state.set_state(WhSt.np_name)
        await _show(target, t["wh_np_dup"].format(name=escape(dup)), kb.cancel_kb(t), edit)
        return
    await _np_update(state, name=name)
    try:
        units = await warehouse.units()                 # Справочник «Ед. изм.» (📏 O'lchov birliklari)
    except Exception:
        logger.exception("O'lchov birliklarini o'qib bo'lmadi")
        units = list(UNITS)
    await state.update_data(np_cats=cats, np_units=units)
    await state.set_state(WhSt.np_cat)
    # 🤖 aqlli tavsiya: avval katalogdagi o'xshash mahsulotlar, keyin kalit so'zlar lug'ati
    hint = (await state.get_data()).get("np_cat_hint")
    sug = hint or suggest_category(name, [(p.name, p.category) for p in catalog])
    if sug:
        sug = next((c for c in cats if c.casefold() == sug.casefold()), sug)
        await _np_update(state, sug=sug)
        await _show(target, t["wh_np_suggest"].format(name=escape(name), cat=escape(cat_label(sug, _lang(t)))),
                    kb.suggest_kb(t), edit)
        return
    await _np_ask_category(target, t, state, cats, edit)


async def _np_ask_category(target: Message, t: Dict[str, str], state: FSMContext, cats: List[str],
                           edit: bool) -> None:
    lang = _lang(t)
    hint = cat_examples(cats, lang)
    text = t["wh_np_cat_pick"] + (f"\n\n{hint}" if hint else "")
    await state.set_state(WhSt.np_cat)
    await _show(target, text, kb.np_category_kb(t, [cat_label(c, lang) for c in cats]), edit)


@router.callback_query(WhSt.np_cat, WhCb.filter(F.a == "sg"))
async def np_suggest_answer(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                            state: FSMContext) -> None:
    data = await state.get_data()
    np = data.get("np") or {}
    await callback.answer()
    if callback_data.k == "yes" and np.get("sug"):
        await _np_update(state, cat=np["sug"])
        await _np_ask_unit(callback.message, t, state, edit=True)
        return
    await _np_ask_category(callback.message, t, state, data.get("np_cats", []), edit=True)


async def _np_ask_unit(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    if ((await state.get_data()).get("np") or {}).get("cat") == kromka.CATEGORY:
        await _np_ask_pack(target, t, state, kromka.UNIT, edit)        # kromka — doim «рулон»
        return
    units = (await state.get_data()).get("np_units") or list(UNITS)
    await state.set_state(WhSt.np_unit)
    await _show(target, t["wh_np_unit"], kb.np_unit_kb(t, units), edit)


@router.callback_query(WhSt.np_cat, WhCb.filter(F.a == "ncat"))
async def np_cat_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext) -> None:
    cats = (await state.get_data()).get("np_cats", [])
    if callback_data.i >= len(cats):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await _np_update(state, cat=cats[callback_data.i])
    await _np_ask_unit(callback.message, t, state, edit=True)
    await callback.answer()


@router.message(WhSt.np_cat, F.text)
async def np_cat_text(message: Message, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    cat = clean_text(message.text, 60)
    if not cat:
        return
    cats = (await state.get_data()).get("np_cats", [])
    lang = _lang(t)
    known = next((c for c in cats if c.casefold() == cat.casefold() or cat_label(c, lang).casefold() == cat.casefold()),
                 None)
    if known is None:
        await warehouse.add_category(cat)          # yozilgan yangi kategoriya umumiy ro'yxatga qo'shiladi
        known = cat
    await _np_update(state, cat=known)
    await _np_ask_unit(message, t, state, edit=False)


async def _np_ask_pack(target: Message, t: Dict[str, str], state: FSMContext, unit: str, edit: bool) -> None:
    await _np_update(state, unit=unit)
    await state.set_state(WhSt.np_pack)
    await _show(target, _pack_prompt(t, unit), kb.pack_kb(t, "pkv"), edit)


@router.callback_query(WhSt.np_unit, WhCb.filter(F.a == "unit"))
async def np_unit_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                       state: FSMContext) -> None:
    units = (await state.get_data()).get("np_units") or list(UNITS)
    unit = units[callback_data.i] if callback_data.i < len(units) else units[0]
    await _np_ask_pack(callback.message, t, state, unit, edit=True)
    await callback.answer()


@router.message(WhSt.np_unit, F.text)
async def np_unit_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    unit = clean_text(message.text, 15)
    if unit:
        await _np_ask_pack(message, t, state, unit, edit=False)


async def _np_ask_min(target: Message, t: Dict[str, str], state: FSMContext, pack: float, edit: bool,
                      warehouse: Optional[WarehouseService] = None) -> None:
    np = await _np_update(state, pack=pack)
    await state.set_state(WhSt.np_min)
    sug = await _norm_suggestion(state, warehouse, np.get("unit", ""), np.get("cat", ""))
    await _show(target, _norm_prompt(t, np.get("name", ""), np.get("unit", "")),
                kb.norm_kb(t, _n(sug) if sug else None, np.get("unit", "")), edit)


@router.callback_query(WhSt.np_pack, WhCb.filter(F.a == "pk1"))
async def np_pack_default(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    await _np_ask_min(callback.message, t, state, 1.0, edit=True, warehouse=warehouse)
    await callback.answer()


@router.message(WhSt.np_pack, F.text)
async def np_pack_text(message: Message, t: Dict[str, str], state: FSMContext,
                       warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _pack_error(t, message.text, value)
    if error:
        await message.answer(error, reply_markup=kb.pack_kb(t, "pkv"))
        return
    await _np_ask_min(message, t, state, value, edit=False, warehouse=warehouse)


async def _np_confirm(target: Message, t: Dict[str, str], state: FSMContext, min_stock: float,
                      edit: bool) -> None:
    np = await _np_update(state, min=min_stock)
    await state.set_state(WhSt.np_confirm)
    text = t["wh_np_confirm"].format(name=escape(np["name"]), cat=escape(cat_label(np.get("cat", ""), _lang(t))),
                                     unit=escape(np.get("unit", "")), pack=_n(np.get("pack", 1)),
                                     min=_n(min_stock))
    await _show(target, text, kb.confirm_kb(t, t["wh_btn_yes"]), edit)


@router.callback_query(WhSt.np_min, WhCb.filter(F.a == "mn0"))
async def np_min_default(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    """Eski xabardagi "0" tugmasi: endi normativ majburiy — tushuntiriladi."""
    await callback.answer(t["wh_min_required_alert"], show_alert=True)


@router.message(WhSt.np_min, F.text)
async def np_min_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    value = parse_number(message.text)
    error = _number_error(t, message.text, value, "min_req")
    if error:
        np = (await state.get_data()).get("np") or {}
        sug = (await state.get_data()).get("norm_sug")
        await message.answer(error, reply_markup=kb.norm_kb(t, _n(sug) if sug else None, np.get("unit", "")))
        return
    await _np_confirm(message, t, state, value, edit=False)


@router.callback_query(WhSt.np_confirm, WhCb.filter(F.a == "ok"))
async def np_save(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                  warehouse: WarehouseService, user: Optional[User] = None) -> None:
    np = (await state.get_data()).get("np") or {}
    if not np.get("name"):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    if float(np.get("min", 0) or 0) <= 0:                   # Мин. норматив majburiy
        await callback.answer(t["wh_min_required_alert"], show_alert=True)
        await _np_ask_min(callback.message, t, state, float(np.get("pack", 1)), edit=True, warehouse=warehouse)
        return
    await callback.answer("⏳")
    try:
        row = await warehouse.add_product(np["name"], np.get("cat", ""), np.get("unit", "шт"),
                                          float(np.get("pack", 1)), float(np.get("min", 0)))
    except DuplicateError:
        await state.set_state(WhSt.np_name)
        await _show(callback.message, t["wh_np_dup"].format(name=escape(np["name"])), kb.cancel_kb(t), True)
        return
    except Exception:
        logger.exception("Mahsulotni qo'shib bo'lmadi")
        await callback.message.answer(t["error"])
        return
    await _remember_norm(warehouse, float(np.get("min", 0)), np.get("unit", ""), np.get("cat", ""))
    if warehouse.product_log is not None:                   # 🆕 Boss hisobotlari uchun
        try:
            await warehouse.product_log.add(warehouse.today(), user.id if user else None,
                                            user.full_name if user else "", np["name"], np.get("cat", ""),
                                            np.get("unit", ""), float(np.get("min", 0)))
        except Exception:
            logger.exception("Yangi mahsulot jurnaliga yozib bo'lmadi")
    ret = np.get("ret")
    flow = ret if ret in (FLOW_IN, FLOW_OUT) else FLOW_IN
    data = await state.get_data()
    # karta: "📥 Shu mahsulotga Kirim kiritish" (yoki Chiqim) + "✏️ Mahsulot ma'lumotlarini tahrirlash".
    # Savat (agar Kirim/Chiqim davomida qo'shilgan bo'lsa) saqlanib qoladi.
    await state.set_state(None)
    await state.update_data(np=None, last_np=np["name"], cont_flow=flow, ed_name=np["name"],
                            cart=data.get("cart", []) if ret else [], flow=flow)
    card = t["wh_np_done"].format(name=escape(np["name"]), row=row,
                                  cat=escape(cat_label(np.get("cat", ""), _lang(t))), unit=escape(np.get("unit", "")),
                                  min=_n(float(np.get("min", 0))))
    await _show(callback.message, card, kb.created_kb(t, flow), True)


# ---------------- ✏️ nomini o'zgartirish / 🗑 o'chirish ----------------
@router.message(WhSt.rn_name, F.text)
async def rename_text(message: Message, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    new = clean_text(message.text, 100)
    if not new:
        return
    old = ((await state.get_data()).get("rn") or {}).get("old")
    if not old:
        await message.answer(t["wh_expired"])
        return
    try:
        names = [p.name for p in await warehouse.catalog(force=True) if p.name != old]
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await message.answer(t["error"])
        return
    dup = exact_match(new, names)
    if dup:
        await message.answer(t["wh_np_dup"].format(name=escape(dup)), reply_markup=kb.cancel_kb(t))
        return
    await state.update_data(act={"kind": "ren", "old": old, "new": new})
    await state.set_state(WhSt.confirm)
    await message.answer(t["wh_rn_confirm"].format(old=escape(old), new=escape(new)),
                         reply_markup=kb.confirm_kb(t, t["wh_btn_yes"]))


@router.callback_query(WhSt.confirm, WhCb.filter(F.a == "ok"))
async def confirm_action(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    act = (await state.get_data()).get("act") or {}
    await callback.answer("⏳")
    try:
        if act.get("kind") == "ren":
            changed = await warehouse.rename_product(act["old"], act["new"])
            if warehouse.product_log is not None:
                await warehouse.product_log.rename(act["old"], act["new"])
            text = t["wh_rn_done"].format(new=escape(act["new"]), count=sum(changed.values()))
            data = await state.get_data()
            if data.get("ed_name") == act["old"]:                  # tahrirlash kartasidan kelingan
                upd = {"ed_name": act["new"]}
                if data.get("last_np") == act["old"]:
                    upd["last_np"] = act["new"]
                await state.update_data(**upd)
                await callback.message.answer(text)
                await _show_edit_card(callback.message, t, state, warehouse, act["new"], edit=True)
                return
        elif act.get("kind") == "del":
            await warehouse.delete_product(act["name"])
            text = t["wh_del_done2"].format(name=escape(act["name"]))
        elif act.get("kind") == "ddel":
            await warehouse.delete_directory_value(act["dk"], act["name"])
            await callback.message.answer(t["wh_dir_deleted"].format(name=escape(act["name"])))
            await _show_dir_menu(callback.message, t, state, warehouse, act["dk"], edit=True)
            return
        elif act.get("kind") == "cdel":
            count = (await warehouse.category_counts()).get(act["name"].casefold(), 0)
            if count:
                text = t["wh_cat_has_products"].format(count=count)
            else:
                await warehouse.delete_category(act["name"])
                text = t["wh_cat_deleted"].format(name=escape(cat_label(act["name"], _lang(t))))
        else:
            await _show(callback.message, t["wh_expired"], None, True)
            await state.clear()
            return
    except DuplicateError as e:
        await callback.message.answer(t["wh_np_dup"].format(name=escape(str(e))))
        return
    except NotFoundError:
        text = t["wh_not_in_catalog"]
    except LastProductError:
        text = t["wh_del_last"]
    except Exception:
        logger.exception("Katalog amalini bajarib bo'lmadi")
        await callback.message.answer(t["error"])
        return
    await state.clear()
    await _show(callback.message, text, None, True)


# ---------------- 17:30 eslatmasidagi tugmalar: 📥 Kirim / 📤 Chiqim kiritish ----------------
@router.callback_query(WhCb.filter(F.a == "go"))
async def go_from_reminder(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], user: User,
                           state: FSMContext, store: UserStore, warehouse: WarehouseService) -> None:
    await callback.answer()
    await _open_flow(callback.message, t, user, state, store, warehouse,
                     FLOW_OUT if callback_data.k == "out" else FLOW_IN)


# ---------------- 💰 narxi yo'q tovarlar ----------------
async def _show_unpriced(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                         edit: bool) -> None:
    try:
        rows = await warehouse.unpriced()
    except Exception:
        logger.exception("Narxi yo'q tovarlarni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    await state.clear()
    await _reconcile_unpriced(warehouse, rows)
    if not rows:
        await _show(target, t["wh_unpriced_none"], None, edit)
        return
    items = _unpriced_queue(warehouse, rows)
    await state.update_data(unp=items, view="unp")
    text = unpriced_list_text(t, items, warehouse.today())
    labels = [f"{it['name']} · {_n(it['qty'])} {it['unit']}" for it in items[:20]]
    await _show(target, text, kb.unpriced_kb(t, labels, len(items)), edit)


@router.callback_query(WhCb.filter(F.a == "unp"))
async def open_unpriced(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    await callback.answer()
    await _show_unpriced(callback.message, t, state, warehouse, edit=True)


@router.callback_query(WhCb.filter(F.a == "up"))
async def pick_unpriced(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    rows = (await state.get_data()).get("unp") or []
    if callback_data.i >= len(rows):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    r = rows[callback_data.i]
    try:
        last = await warehouse.last_price(r["name"])
    except Exception:
        last = None
    await state.update_data(unp_cur=r, unp_last=last)
    await state.set_state(WhSt.unp_price)
    await callback.answer()
    await _show(callback.message, t["wh_unpriced_prompt"].format(
        name=escape(r["name"]), date=r["day"], qty=_n(r["qty"]), unit=escape(r["unit"]),
        supplier=escape(r["sup"] or "—")), kb.unpriced_price_kb(t, _n(last) if last else None), True)


async def _save_unpriced(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                         price: float) -> None:
    r = (await state.get_data()).get("unp_cur")
    if not r:
        await target.answer(t["wh_expired"])
        return
    try:
        total = await warehouse.set_price(r["row"], r["name"], price)
    except NotFoundError:
        await target.answer(t["wh_unpriced_moved"])
        await _show_unpriced(target, t, state, warehouse, edit=False)
        return
    except Exception:
        logger.exception("Narxni jadvalga yozib bo'lmadi")
        await target.answer(t["wh_save_error"])
        return
    await _resolve_unpriced(warehouse, r)
    await target.answer(t["wh_unpriced_saved"].format(name=escape(r["name"]), price=_n(price), qty=_n(r["qty"]),
                                                      total=_n(total)))
    await _show_unpriced(target, t, state, warehouse, edit=False)


@router.message(WhSt.unp_price, F.text)
async def unpriced_price_text(message: Message, t: Dict[str, str], state: FSMContext,
                              warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _number_error(t, message.text, value, "price")
    if error:
        await message.answer(error, reply_markup=kb.cancel_kb(t))
        return
    await _save_unpriced(message, t, state, warehouse, value)


@router.callback_query(WhSt.unp_price, WhCb.filter(F.a == "ulp"))
async def unpriced_last_price(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                              warehouse: WarehouseService) -> None:
    last = (await state.get_data()).get("unp_last")
    if not last:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    await _save_unpriced(callback.message, t, state, warehouse, float(last))


# ---------------- 🏷 Kategoriyalar (umumiy, butun katalog uchun) ----------------
async def _show_cat_menu(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                         edit: bool) -> None:
    try:
        cats = await warehouse.all_categories()
        counts = await warehouse.category_counts()
    except Exception:
        logger.exception("Kategoriyalarni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    await state.clear()
    await state.update_data(cm_cats=cats)
    lang = _lang(t)
    lines = [f"• {escape(cat_label(c, lang))} — {counts.get(c.casefold(), 0)}" for c in cats]
    await _show(target, t["wh_cat_menu"].format(count=len(cats), list="\n".join(lines) or "—"),
                kb.cat_menu_kb(t), edit)


@router.callback_query(WhCb.filter(F.a == "cm"))
async def open_cat_menu(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    await callback.answer()
    await _show_cat_menu(callback.message, t, state, warehouse, edit=True)


@router.callback_query(WhCb.filter(F.a == "cmk"))
async def cat_menu_action(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    await callback.answer()
    if callback_data.k == "add":
        await state.set_state(WhSt.cat_name)
        await _show(callback.message, t["wh_cat_add_prompt"], kb.cancel_kb(t), True)
        return
    cats = (await state.get_data()).get("cm_cats") or await warehouse.all_categories()
    await state.update_data(cm_cats=cats, cm_act=callback_data.k)
    prompt = t["wh_cat_pick_rename"] if callback_data.k == "ren" else t["wh_cat_pick_delete"]
    await _show(callback.message, prompt, kb.cat_pick_kb(t, [cat_label(c, _lang(t)) for c in cats]), True)


@router.callback_query(WhCb.filter(F.a == "cpk"))
async def cat_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                   warehouse: WarehouseService) -> None:
    data = await state.get_data()
    cats = data.get("cm_cats") or []
    if callback_data.i >= len(cats):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    name = cats[callback_data.i]
    shown = escape(cat_label(name, _lang(t)))
    if data.get("cm_act") == "ren":
        await state.update_data(cm_old=name)
        await state.set_state(WhSt.cat_rn)
        await callback.answer()
        await _show(callback.message, t["wh_cat_rn_prompt"].format(name=shown), kb.cancel_kb(t), True)
        return
    try:
        count = (await warehouse.category_counts()).get(name.casefold(), 0)
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await callback.answer(t["error"], show_alert=True)
        return
    if count:
        await callback.answer(t["wh_cat_has_products"].format(count=count), show_alert=True)
        return
    await callback.answer()
    await state.update_data(act={"kind": "cdel", "name": name})
    await state.set_state(WhSt.confirm)
    await _show(callback.message, t["wh_cat_del_confirm"].format(name=shown),
                kb.confirm_kb(t, t["wh_btn_yes_delete"]), True)


def _cat_exists(name: str, cats: List[str], lang: str, skip: Optional[str] = None) -> Optional[str]:
    for c in cats:
        if c == skip:
            continue
        if c.casefold() == name.casefold() or cat_label(c, lang).casefold() == name.casefold():
            return c
    return None


@router.message(WhSt.cat_name, F.text)
async def cat_add_text(message: Message, t: Dict[str, str], state: FSMContext,
                       warehouse: WarehouseService) -> None:
    name = clean_text(message.text, 60)
    if not name:
        return
    try:
        cats = await warehouse.all_categories()
    except Exception:
        logger.exception("Kategoriyalarni o'qib bo'lmadi")
        await message.answer(t["error"])
        return
    dup = _cat_exists(name, cats, _lang(t))
    if dup:
        await message.answer(t["wh_cat_dup"].format(name=escape(cat_label(dup, _lang(t)))),
                             reply_markup=kb.cancel_kb(t))
        return
    await warehouse.add_category(name)
    await state.clear()
    await message.answer(t["wh_cat_added"].format(name=escape(name)))
    await _show_cat_menu(message, t, state, warehouse, edit=False)


@router.message(WhSt.cat_rn, F.text)
async def cat_rename_text(message: Message, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    new = clean_text(message.text, 60)
    old = (await state.get_data()).get("cm_old")
    if not new:
        return
    if not old:
        await message.answer(t["wh_expired"])
        return
    try:
        cats = await warehouse.all_categories()
    except Exception:
        logger.exception("Kategoriyalarni o'qib bo'lmadi")
        await message.answer(t["error"])
        return
    dup = _cat_exists(new, cats, _lang(t), skip=old)
    if dup:
        await message.answer(t["wh_cat_dup"].format(name=escape(cat_label(dup, _lang(t)))),
                             reply_markup=kb.cancel_kb(t))
        return
    await message.answer("⏳")
    try:
        count = await warehouse.rename_category(old, new)       # jadvaldagi barcha mos kataklar + ro'yxat
    except Exception:
        logger.exception("Kategoriyani qayta nomlab bo'lmadi")
        await message.answer(t["wh_save_error"])
        return
    await state.clear()
    await message.answer(t["wh_cat_renamed"].format(old=escape(cat_label(old, _lang(t))), new=escape(new),
                                                    count=count))
    await _show_cat_menu(message, t, state, warehouse, edit=False)


# ---------------- 👷 Ustalar / 📏 O'lchov birliklari (Справочник ustunlari) ----------------
_DIR_KINDS = (MASTER, UNIT_KIND)


async def _show_dir_menu(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                         kind: str, edit: bool) -> None:
    kind = kind if kind in _DIR_KINDS else MASTER
    try:
        values = await warehouse.directory(kind)
    except Exception:
        logger.exception("Справочник ni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    await state.clear()
    await state.update_data(dm_kind=kind, dm_vals=values)
    title = t["wh_dir_title_master" if kind == MASTER else "wh_dir_title_unit"].format(n=len(values))
    body = "\n".join(f"{i}. {escape(v)}" for i, v in enumerate(values, start=1)) or t["wh_dir_empty"]
    await _show(target, f"{title}\n\n{body}", kb.dir_menu_kb(t, kind, bool(values)), edit)


@router.callback_query(WhCb.filter(F.a == "pmm"))
async def back_to_pm_menu(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _show(callback.message, t["wh_pm_title"], kb.pm_menu_kb(t), True)


@router.callback_query(WhCb.filter(F.a == "dm"))
async def open_dir_menu(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    await callback.answer()
    await _show_dir_menu(callback.message, t, state, warehouse, callback_data.k, edit=True)


@router.callback_query(WhCb.filter(F.a == "dma"))
async def dir_add_prompt(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                         state: FSMContext) -> None:
    kind = callback_data.k if callback_data.k in _DIR_KINDS else MASTER
    await state.update_data(dm_kind=kind)
    await state.set_state(WhSt.dir_add)
    await callback.answer()
    await _show(callback.message, t["wh_dir_add_master" if kind == MASTER else "wh_dir_add_unit"],
                kb.cancel_kb(t), True)


@router.message(WhSt.dir_add, F.text)
async def dir_add_text(message: Message, t: Dict[str, str], state: FSMContext,
                       warehouse: WarehouseService) -> None:
    value = clean_text(message.text, 40 if (await state.get_data()).get("dm_kind") == UNIT_KIND else 60)
    if not value:
        return
    kind = (await state.get_data()).get("dm_kind", MASTER)
    try:
        added = await warehouse.add_directory_value(kind, value)
    except Exception:
        logger.exception("Справочник ga yozib bo'lmadi")
        await message.answer(t["wh_save_error"])
        return
    if not added:
        await message.answer(t["wh_dir_dup"].format(name=escape(value)), reply_markup=kb.cancel_kb(t))
        return
    await message.answer(t["wh_dir_added"].format(name=escape(value)))
    await _show_dir_menu(message, t, state, warehouse, kind, edit=False)


@router.callback_query(WhCb.filter(F.a == "dmd"))
async def dir_delete_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    kind = callback_data.k if callback_data.k in _DIR_KINDS else MASTER
    values = await warehouse.directory(kind)
    await state.update_data(dm_kind=kind, dm_vals=values)
    await callback.answer()
    await _show(callback.message, t["wh_dir_pick_del"], kb.dir_pick_kb(t, kind, values), True)


@router.callback_query(WhCb.filter(F.a == "dmx"))
async def dir_delete_confirm(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                             state: FSMContext) -> None:
    data = await state.get_data()
    values = data.get("dm_vals") or []
    if callback_data.i >= len(values):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    name = values[callback_data.i]
    await state.update_data(act={"kind": "ddel", "dk": data.get("dm_kind", MASTER), "name": name})
    await state.set_state(WhSt.confirm)
    await callback.answer()
    await _show(callback.message, t["wh_dir_del_confirm"].format(name=escape(name)),
                kb.confirm_kb(t, t["wh_btn_yes_delete"]), True)


# ---------------- ⬅️ Orqaga: har bir qadamda bitta qadam ortga ----------------
@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "back"))
async def step_back(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                    warehouse: WarehouseService) -> None:
    await callback.answer()
    st = await state.get_state()
    data = await state.get_data()
    flow = data.get("flow", FLOW_IN)
    cur = data.get("cur") or {}
    msg = callback.message

    # --- Kirim / Chiqim ---
    if st == WhSt.mode.state:
        await _show_categories(msg, t, state, warehouse, edit=True)
    elif st == WhSt.qty.state:
        if flow == FLOW_IN and float(cur.get("pack") or 1) > 1:
            await _show_mode(msg, t, state, edit=True)
        else:
            await _show_categories(msg, t, state, warehouse, edit=True)
    elif st == WhSt.price.state:
        await _ask_qty(msg, t, state, edit=True)
    elif st == WhSt.who.state:
        if flow == FLOW_IN:
            await _ask_price(msg, t, state, warehouse, edit=True)
        else:
            await _ask_qty(msg, t, state, edit=True)
    elif st == WhSt.purpose.state:
        await _ask_who(msg, t, state, warehouse, edit=True)
    elif st == WhSt.search.state and data.get("view") == "cart" and data.get("cart"):
        # savat: oxirgi qo'shilgan tovar savatdan olinib, oxirgi qadami qayta ochiladi
        cart = list(data["cart"])
        last = cart.pop()
        await state.update_data(cart=cart, cur=last, view="item")
        if flow == FLOW_IN:
            await _ask_who(msg, t, state, warehouse, edit=True)
        else:
            await _ask_purpose(msg, t, state, edit=True)

    # --- ➕ Yangi mahsulot ---
    elif st == WhSt.np_name.state:
        ret = (data.get("np") or {}).get("ret")
        if ret in (FLOW_IN, FLOW_OUT):
            await state.update_data(flow=ret, np=None)
            await _show_categories(msg, t, state, warehouse, edit=True)
        else:
            await state.clear()
            await _show(msg, t["wh_pm_title"], kb.pm_menu_kb(t), True)
    elif st == WhSt.np_cat.state:
        await state.set_state(WhSt.np_name)
        await _show(msg, t["wh_np_name"], kb.cancel_kb(t), True)
    elif st == WhSt.np_unit.state:
        await _np_ask_category(msg, t, state, data.get("np_cats", []), edit=True)
    elif st == WhSt.np_pack.state:
        if (data.get("np") or {}).get("cat") == kromka.CATEGORY:
            await _np_ask_category(msg, t, state, data.get("np_cats", []), edit=True)
        else:
            await _np_ask_unit(msg, t, state, edit=True)
    elif st == WhSt.np_min.state and (data.get("np") or {}).get("krm"):
        await _krm_new_prompt(msg, t, state, warehouse, edit=True)
    elif st == WhSt.np_min.state:
        await _np_ask_pack(msg, t, state, (data.get("np") or {}).get("unit", "шт"), edit=True)
    elif st == WhSt.np_confirm.state:
        await _np_ask_min(msg, t, state, float((data.get("np") or {}).get("pack", 1)), edit=True,
                          warehouse=warehouse)

    # --- tahrirlash / o'chirish / kategoriyalar / narxlar ---
    elif st == WhSt.rn_name.state:
        if data.get("ed_name"):
            await _show_edit_card(msg, t, state, warehouse, data["ed_name"], edit=True)
        else:
            await _show_categories(msg, t, state, warehouse, edit=True)
    elif st == WhSt.kir_pack.state:
        if float(cur.get("pack") or 1) > 1:
            await _show_mode(msg, t, state, edit=True)
        else:
            await _ask_qty(msg, t, state, edit=True)
    elif st == WhSt.krm_size.state:
        await _show_categories(msg, t, state, warehouse, edit=True)
    elif st == WhSt.krm_color.state:
        await _krm_sizes(msg, t, state, edit=True)
    elif st == WhSt.krm_new.state:
        await _krm_colors(msg, t, state, warehouse, data.get("krm_size") or kromka.SIZES[0], edit=True)
    elif st in (WhSt.ed_unit.state, WhSt.ed_cat.state, WhSt.ed_min.state, WhSt.ed_pack.state,
                WhSt.ed_price.state, WhSt.ed_adj.state):
        await _show_edit_card(msg, t, state, warehouse, data.get("ed_name", ""), edit=True)
    elif st == WhSt.ed_card.state:
        await state.clear()
        await _show(msg, t["wh_pm_title"], kb.pm_menu_kb(t), True)
    elif st == WhSt.over.state:
        await _ask_qty(msg, t, state, edit=True)
    elif st == WhSt.confirm.state:
        kind = (data.get("act") or {}).get("kind")
        if kind == "ren" and data.get("ed_name"):
            await _show_edit_card(msg, t, state, warehouse, data["ed_name"], edit=True)
        elif kind == "ren":
            await state.set_state(WhSt.rn_name)
            await _show(msg, t["wh_rn_prompt"].format(name=escape(data["act"]["old"])), kb.cancel_kb(t), True)
        elif kind == "cdel":
            await _show_cat_menu(msg, t, state, warehouse, edit=True)
        elif kind == "ddel":
            await _show_dir_menu(msg, t, state, warehouse, data["act"]["dk"], edit=True)
        else:
            await _show_categories(msg, t, state, warehouse, edit=True)
    elif st == WhSt.dir_add.state:
        await _show_dir_menu(msg, t, state, warehouse, data.get("dm_kind", MASTER), edit=True)
    elif st in (WhSt.cat_name.state, WhSt.cat_rn.state):
        await _show_cat_menu(msg, t, state, warehouse, edit=True)
    elif st == WhSt.unp_price.state:
        await _show_unpriced(msg, t, state, warehouse, edit=True)
    elif st == WhSt.seq_price.state:
        i = int(data.get("seq_i", 0))
        if i <= 0:
            await _show_unpriced(msg, t, state, warehouse, edit=True)
        else:
            await _seq_show(msg, t, state, warehouse, i - 1, edit=True)
    else:
        await _show_categories(msg, t, state, warehouse, edit=True)


# ---------------- 🛡 aqlli yo'riqnoma: tushunarli xatolar ----------------
def _available(data: Dict[str, Any], cur: Dict[str, Any]) -> float:
    """📤 Chiqim uchun mavjud qoldiq: jadvaldagi qoldiq − shu savatda oldin olingani."""
    taken = sum(it["qty"] for it in data.get("cart", []) if it["name"] == cur.get("name"))
    return float(cur.get("stock") or 0) - taken


def _number_error(t: Dict[str, str], raw: str, value: Optional[float], field: str, unit: str = "") -> Optional[str]:
    """Son kutilganda: nima xato bo'lgani va nima qilish kerakligi (aniq misollar bilan). Xato yo'q — None.
    field: qty | price | pack | min (min uchun 0 ham mumkin)."""
    example = t[f"wh_ex_{field}"].format(unit=escape(unit) or "")
    if value is None:
        return t["wh_err_not_number"].format(raw=escape(str(raw)[:30]), example=example)
    if field == "min_req" and value <= 0:
        # har bir ombor tovarida kuzatuv chegarasi bo'lishi shart
        return t["wh_err_min_required"].format(example=example)
    if value < 0 or (value == 0 and field != "min"):
        return t["wh_err_positive" if field != "min" else "wh_err_negative"].format(example=example)
    limit = {"qty": 10**9, "price": 10**13, "pack": 10**6, "min": 10**9, "min_req": 10**9}[field]
    if value > limit:
        return t["wh_err_too_big"].format(example=example)
    return None


@router.callback_query(WhCb.filter(F.a == "srch"))
async def search_again(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await state.set_state(WhSt.search)
    await state.update_data(view="pick")
    await callback.answer()
    await _show(callback.message, t["wh_search_again"], kb.short_search_kb(t), True)


@router.callback_query(WhCb.filter(F.a == "tokir"))
async def zero_stock_to_inflow(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                               warehouse: WarehouseService) -> None:
    """Qoldiq 0: shu mahsulot uchun 📥 Kirim ga o'tish (Chiqim savati bo'sh bo'lsa)."""
    data = await state.get_data()
    cur = data.get("cur") or {}
    if data.get("cart"):
        await callback.answer(t["wh_cart_switch_warn"], show_alert=True)
        return
    try:
        product = await warehouse.product(cur.get("name", ""))
    except Exception:
        product = None
    await callback.answer()
    await state.update_data(flow=FLOW_IN, cart=[])
    if product is None:
        await _show_categories(callback.message, t, state, warehouse, edit=True)
        return
    await _start_item(callback.message, t, state, product, edit=True)


@router.callback_query(WhSt.over, WhCb.filter(F.a == "qall"))
async def take_all(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                   warehouse: WarehouseService) -> None:
    data = await state.get_data()
    cur = dict(data.get("cur") or {})
    avail = _available(data, cur)
    if not cur or avail <= 0:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    cur["qty"] = cur["qty_in"] = avail
    await state.update_data(cur=cur)
    await callback.answer()
    await _ask_who(callback.message, t, state, warehouse, edit=True)


@router.callback_query(WhSt.over, WhCb.filter(F.a == "qre"))
async def other_qty(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await callback.answer()
    await _ask_qty(callback.message, t, state, edit=True)


@router.message(WhSt.over, F.text)
async def over_text(message: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService) -> None:
    """Tugma o'rniga yangi son yozilsa — oddiy miqdor kiritish kabi."""
    await state.set_state(WhSt.qty)
    await enter_qty(message, t, state, warehouse)


# ---------------- ➕ yangi mahsulotdan keyin: davom etish ----------------
@router.callback_query(WhCb.filter(F.a == "cont"))
async def continue_with_new(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                            warehouse: WarehouseService) -> None:
    data = await state.get_data()
    name = data.get("last_np") or data.get("ed_name")
    try:
        product = await warehouse.product(name or "", force=True)
    except Exception:
        product = None
    if product is None:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await state.update_data(flow=data.get("cont_flow", FLOW_IN), cart=data.get("cart", []))
    await _start_item(callback.message, t, state, product, edit=True)


# ---------------- ✏️ mahsulot ma'lumotlarini tahrirlash (nom, birlik, kategoriya, Мин. норматив) ----------------
async def _show_edit_card(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                          name: str, edit: bool) -> None:
    try:
        product = await warehouse.product(name, force=True)
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    if product is None:
        await _show(target, t["wh_not_in_catalog"], None, edit)
        return
    data = await state.get_data()
    await state.set_state(WhSt.ed_card)
    await state.update_data(ed_name=product.name, rn={"old": product.name}, view="item")
    price = _n(product.avg_price) if product.avg_price else "—"
    text = t["wh_ed_card"].format(name=escape(product.name), unit=escape(product.unit),
                                  cat=escape(cat_label(product.category, _lang(t)) if product.category else "—"),
                                  min=_n(product.min_stock), stock=_n(product.stock), pack=_n(product.pack),
                                  price=price)
    can_continue = data.get("cont_flow") if data.get("last_np") == product.name else None
    await _show(target, text, kb.edit_kb(t, can_continue, pack=_n(product.pack), unit=product.unit, price=price,
                                         stock=_n(product.stock)), edit)


@router.callback_query(WhCb.filter(F.a == "ed"))
async def open_edit_card(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    name = (await state.get_data()).get("ed_name")
    if not name:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _show_edit_card(callback.message, t, state, warehouse, name, edit=True)


@router.callback_query(WhCb.filter(F.a == "ef"))
async def edit_field(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                     warehouse: WarehouseService) -> None:
    data = await state.get_data()
    name = data.get("ed_name")
    if not name:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    product = await warehouse.product(name)
    if product is None:
        await _show(callback.message, t["wh_not_in_catalog"], None, True)
        return
    if callback_data.k == "pack":
        await state.set_state(WhSt.ed_pack)
        await _show(callback.message, _pack_prompt(t, product.unit) + "\n\n" + t["wh_pack_now"].format(
            pack=_n(product.pack), unit=escape(product.unit)), kb.pack_kb(t, "epk"), True)
        return
    if callback_data.k == "price":
        await _ask_edit_price(callback.message, t, state, product, edit=True, warehouse=warehouse)
        return
    if callback_data.k == "adj":
        await _ask_adj(callback.message, t, state, await warehouse.product(name, force=True) or product, edit=True)
        return
    if callback_data.k == "name":
        await state.update_data(rn={"old": name})
        await state.set_state(WhSt.rn_name)
        await _show(callback.message, t["wh_rn_prompt"].format(name=escape(name)), kb.cancel_kb(t), True)
    elif callback_data.k == "unit":
        units = await warehouse.units()
        await state.update_data(ed_units=units)
        await state.set_state(WhSt.ed_unit)
        await _show(callback.message, t["wh_ed_unit_prompt"].format(name=escape(name), unit=escape(product.unit)),
                    kb.pick_kb(t, units, "eu", current=product.unit), True)
    elif callback_data.k == "cat":
        cats = await warehouse.all_categories()
        labels = [cat_label(c, _lang(t)) for c in cats]
        await state.update_data(ed_cats=cats)
        await state.set_state(WhSt.ed_cat)
        current = cat_label(product.category, _lang(t)) if product.category else ""
        await _show(callback.message, t["wh_ed_cat_prompt"].format(name=escape(name), examples=cat_examples(cats, _lang(t))),
                    kb.pick_kb(t, labels, "ec", current=current), True)
    else:
        await state.set_state(WhSt.ed_min)
        sug = await _norm_suggestion(state, warehouse, product.unit, product.category)
        prompt = _norm_prompt(t, name, product.unit) + "\n\n" + t["wh_ed_min_current"].format(
            min=_n(product.min_stock), unit=escape(product.unit))
        await _show(callback.message, prompt, kb.norm_kb(t, _n(sug) if sug else None, product.unit), True)


async def _apply_edit(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                      **fields: Any) -> None:
    name = (await state.get_data()).get("ed_name", "")
    try:
        await warehouse.update_product(name, **fields)
    except NotFoundError:
        await _show(target, t["wh_not_in_catalog"], None, True)
        return
    except Exception:
        logger.exception("Mahsulot ma'lumotini yozib bo'lmadi")
        await target.answer(t["wh_save_error"])
        return
    await target.answer(t["wh_ed_saved"])
    await _show_edit_card(target, t, state, warehouse, name, edit=False)


@router.callback_query(WhSt.ed_unit, WhCb.filter(F.a == "eu"))
async def edit_unit_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    units = (await state.get_data()).get("ed_units") or []
    if callback_data.i >= len(units):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    await _apply_edit(callback.message, t, state, warehouse, unit=units[callback_data.i])


@router.message(WhSt.ed_unit, F.text)
async def edit_unit_text(message: Message, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    unit = clean_text(message.text, 20)
    if unit:
        await _apply_edit(message, t, state, warehouse, unit=unit)


@router.callback_query(WhSt.ed_cat, WhCb.filter(F.a == "ec"))
async def edit_cat_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    cats = (await state.get_data()).get("ed_cats") or []
    if callback_data.i >= len(cats):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    await _apply_edit(callback.message, t, state, warehouse, category=cats[callback_data.i])


async def _edit_min(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                    value: float) -> None:
    name = (await state.get_data()).get("ed_name", "")
    product = await warehouse.product(name)
    await _remember_norm(warehouse, value, product.unit if product else "", product.category if product else "")
    await _apply_edit(target, t, state, warehouse, min_stock=value)


@router.message(WhSt.ed_min, F.text)
async def edit_min_text(message: Message, t: Dict[str, str], state: FSMContext,
                        warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _number_error(t, message.text, value, "min_req")
    if error:
        sug = (await state.get_data()).get("norm_sug")
        await message.answer(error, reply_markup=kb.norm_kb(t, _n(sug) if sug else None, ""))
        return
    await _edit_min(message, t, state, warehouse, value)


@router.callback_query(WhSt.ed_min, WhCb.filter(F.a.in_({"mns", "mn0"})))
async def edit_min_button(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    if callback_data.a == "mn0":
        await callback.answer(t["wh_min_required_alert"], show_alert=True)
        return
    sug = (await state.get_data()).get("norm_sug")
    if not sug:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    await _edit_min(callback.message, t, state, warehouse, float(sug))


@router.callback_query(WhSt.np_min, WhCb.filter(F.a == "mns"))
async def np_min_suggested(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    sug = (await state.get_data()).get("norm_sug")
    if not sug:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _np_confirm(callback.message, t, state, float(sug), edit=True)


# ---------------- Мин. норматив: tushuntirish va eslab qolish ----------------
def _norm_prompt(t: Dict[str, str], name: str, unit: str) -> str:
    return t["wh_norm_prompt"].format(name=escape(name), unit=escape(unit)) + "\n\n" + t["wh_norm_why"]


async def _norm_suggestion(state: FSMContext, warehouse: Optional[WarehouseService], unit: str,
                           category: str) -> Optional[float]:
    sug = None
    store = getattr(warehouse, "norm_store", None) if warehouse is not None else None
    if store is not None:
        try:
            sug = await store.suggest(unit, category)
        except Exception:
            logger.exception("Normativ tavsiyasini o'qib bo'lmadi")
    await state.update_data(norm_sug=sug)
    return sug


async def _remember_norm(warehouse: WarehouseService, value: float, unit: str, category: str) -> None:
    store = getattr(warehouse, "norm_store", None)
    if store is None:
        return
    try:
        await store.remember(value, unit, category)
    except Exception:
        logger.exception("Normativni eslab qolib bo'lmadi")


# ---------------- 💰 narxsiz kirimlar: nazorat jadvali (SQLite) bilan moslash ----------------
def _unpriced_queue(warehouse: WarehouseService, rows: List[Any]) -> List[Dict[str, Any]]:
    """O'tgan kunlardan qolganlar (eng eskisi) birinchi, keyin bugungilar."""
    today = warehouse.today()
    items = [{"row": r.row, "name": r.name, "unit": r.unit, "qty": r.qty, "iso": r.day.isoformat() if r.day else "",
              "day": r.day.strftime("%d.%m.%Y") if r.day else "—", "sup": r.supplier,
              "late": (today - r.day).days if r.day and r.day < today else 0} for r in rows]
    return sorted(items, key=lambda it: (-it["late"], it["row"]))


def unpriced_list_text(t: Dict[str, str], items: List[Dict[str, Any]], today, limit: int = 25) -> str:
    """Bugungi va «📅 O'tgan kunlardan qolib ketganlar» (sana va necha kun o'tgani bilan) alohida."""
    late = [it for it in items if it["late"] > 0]
    fresh = [it for it in items if it["late"] == 0]
    lines = [t["wh_unpriced_title2"].format(count=len(items))]
    if fresh:
        lines += ["", t["wh_unp_today"].format(n=len(fresh))]
        lines += [f"• {escape(it['name'])} — {_n(it['qty'])} {escape(it['unit'])}" for it in fresh[:limit]]
    if late:
        lines += ["", t["wh_unp_late"].format(n=len(late))]
        lines += [t["wh_unp_late_line"].format(name=escape(it["name"]), qty=_n(it["qty"]), unit=escape(it["unit"]),
                                               date=it["day"], days=it["late"]) for it in late[:limit]]
    lines += ["", t["wh_unp_hint"]]
    return "\n".join(lines)


async def _reconcile_unpriced(warehouse: WarehouseService, rows: List[Any]) -> None:
    store = warehouse.unpriced_store
    if store is None:
        return
    try:
        today = warehouse.today().isoformat()
        await store.reconcile([(r.row, r.name, r.qty, r.unit, r.day.isoformat() if r.day else today) for r in rows])
    except Exception:
        logger.exception("Narxsiz kirimlar jadvalini moslab bo'lmadi")


async def _resolve_unpriced(warehouse: WarehouseService, it: Dict[str, Any]) -> None:
    if warehouse.unpriced_store is None:
        return
    try:
        await warehouse.unpriced_store.resolve(it["name"], it.get("iso") or warehouse.today().isoformat(), it["qty"])
    except Exception:
        logger.exception("Narxsiz kirimni hal bo'lgan deb belgilab bo'lmadi")


async def _track_inflow(warehouse: WarehouseService, items: List[InflowItem], rows: List[int]) -> None:
    """Kirim saqlangach: narxsiz qatorlar nazoratga olinadi; yangi mahsulotning birinchi kirimi yoziladi."""
    today = warehouse.today().isoformat()
    for it, row in zip(items, rows):
        try:
            if not it.price and warehouse.unpriced_store is not None:
                await warehouse.unpriced_store.add(row, it.name, it.qty, it.unit, today)
            if warehouse.product_log is not None:
                await warehouse.product_log.note_first_inflow(it.name, it.qty, it.price)
        except Exception:
            logger.exception("Kirim nazorat yozuvlarini saqlab bo'lmadi")


# ---------------- 💰 narxlarni birma-bir kiritish ----------------
async def _seq_show(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                    i: int, edit: bool) -> None:
    items = (await state.get_data()).get("unp") or []
    if i >= len(items):
        await state.clear()
        try:
            left = len(await warehouse.unpriced())
        except Exception:
            left = 0
        await _show(target, t["wh_seq_done"] if not left else t["wh_seq_done_left"].format(n=left), None, edit)
        return
    it = items[i]
    try:
        last = await warehouse.last_price(it["name"])
    except Exception:
        last = None
    await state.set_state(WhSt.seq_price)
    await state.update_data(seq_i=i, unp_cur=it, unp_last=last)
    late = t["wh_seq_late"].format(days=it["late"]) if it.get("late") else ""
    text = t["wh_seq_prompt"].format(i=i + 1, n=len(items), name=escape(it["name"]), qty=_n(it["qty"]),
                                     unit=escape(it["unit"]), date=it["day"], late=late)
    await _show(target, text, kb.seq_kb(t, _n(last) if last else None), edit)


@router.callback_query(WhCb.filter(F.a == "seq"))
async def seq_start(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                    warehouse: WarehouseService) -> None:
    data = await state.get_data()
    if not data.get("unp"):                                  # eslatma xabaridan bosilgan: ro'yxat yangidan
        try:
            rows = await warehouse.unpriced()
        except Exception:
            logger.exception("Narxi yo'q tovarlarni o'qib bo'lmadi")
            await callback.answer(t["error"], show_alert=True)
            return
        await _reconcile_unpriced(warehouse, rows)
        if not rows:
            await callback.answer(t["wh_unpriced_none"], show_alert=True)
            return
        await state.clear()
        await state.update_data(unp=_unpriced_queue(warehouse, rows))
    await callback.answer()
    await _seq_show(callback.message, t, state, warehouse, 0, edit=True)


async def _seq_save(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                    price: float) -> None:
    data = await state.get_data()
    it, i = data.get("unp_cur"), int(data.get("seq_i", 0))
    if not it:
        await target.answer(t["wh_expired"])
        return
    try:
        total = await warehouse.set_price(it["row"], it["name"], price)
    except NotFoundError:
        await target.answer(t["wh_unpriced_moved"])
        await _seq_show(target, t, state, warehouse, i + 1, edit=False)
        return
    except Exception:
        logger.exception("Narxni jadvalga yozib bo'lmadi")
        await target.answer(t["wh_save_error"])
        return
    await _resolve_unpriced(warehouse, it)
    await target.answer(t["wh_unpriced_saved"].format(name=escape(it["name"]), price=_n(price), qty=_n(it["qty"]),
                                                      total=_n(total)))
    await _seq_show(target, t, state, warehouse, i + 1, edit=False)


@router.message(WhSt.seq_price, F.text)
async def seq_price_text(message: Message, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _number_error(t, message.text, value, "price")
    if error:
        last = (await state.get_data()).get("unp_last")
        await message.answer(error, reply_markup=kb.seq_kb(t, _n(last) if last else None))
        return
    await _seq_save(message, t, state, warehouse, value)


@router.callback_query(WhSt.seq_price, WhCb.filter(F.a == "sqlp"))
async def seq_last_price(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    last = (await state.get_data()).get("unp_last")
    if not last:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    await _seq_save(callback.message, t, state, warehouse, float(last))


@router.callback_query(WhSt.seq_price, WhCb.filter(F.a == "sqs"))
async def seq_skip(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                   warehouse: WarehouseService) -> None:
    await callback.answer()
    await _seq_show(callback.message, t, state, warehouse, int((await state.get_data()).get("seq_i", 0)) + 1,
                    edit=True)


# ---------------- 🔎 erkin matn bilan topilgan mahsulot: tezkor tugmalar ----------------
@router.callback_query(WhCb.filter(F.a == "lk"))
async def lookup_action(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], user: User,
                        state: FSMContext, store: UserStore, warehouse: WarehouseService) -> None:
    names = (await state.get_data()).get("lookup") or []
    if callback_data.i >= len(names):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    try:
        product = await warehouse.product(names[callback_data.i], force=True)
    except Exception:
        product = None
    if product is None:
        await callback.answer(t["wh_not_in_catalog"], show_alert=True)
        return
    await callback.answer()
    await state.clear()
    store.clear_awaiting(user.id)
    if callback_data.k == "ed":
        await _show_edit_card(callback.message, t, state, warehouse, product.name, edit=False)
        return
    if callback_data.k == "pr":
        await state.update_data(ed_name=product.name)
        await _ask_edit_price(callback.message, t, state, product, edit=False, warehouse=warehouse)
        return
    if callback_data.k == "adj":
        await _ask_adj(callback.message, t, state, product, edit=False)
        return
    await state.update_data(flow=FLOW_OUT if callback_data.k == "out" else FLOW_IN, cart=[])
    await _start_item(callback.message, t, state, product, edit=False)


@router.callback_query(WhCb.filter(F.a == "lkadd"))
async def lookup_add(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                     warehouse: WarehouseService) -> None:
    query = (await state.get_data()).get("lookup_q")
    if not query:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.update_data(np={"ret": None}, cart=[])
    await _np_set_name(callback.message, t, state, warehouse, query, edit=False)


# ---------------- 📦 karobkadagi miqdor (В упаковке) ----------------
def _pack_prompt(t: Dict[str, str], unit: str) -> str:
    return t["wh_pack_prompt"].format(unit=escape(unit or "шт")) + "\n\n" + t["wh_pack_why"]


def _pack_error(t: Dict[str, str], raw: str, value: Optional[float]) -> Optional[str]:
    """Karobkadagi miqdor: butun son, 0 dan katta."""
    error = _number_error(t, raw, value, "pack")
    if error:
        return error
    if not float(value).is_integer():
        return t["wh_err_pack_int"]
    return None


@router.callback_query(WhSt.np_pack, WhCb.filter(F.a == "pkv"))
async def np_pack_preset(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    await callback.answer()
    await _np_ask_min(callback.message, t, state, float(max(1, callback_data.i)), edit=True, warehouse=warehouse)


# ---- ✏️ tahrirlash: karobkadagi miqdor ----
async def _apply_pack(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                      value: float) -> None:
    await _apply_edit(target, t, state, warehouse, pack=value)


@router.callback_query(WhSt.ed_pack, WhCb.filter(F.a == "epk"))
async def edit_pack_preset(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                           warehouse: WarehouseService) -> None:
    await callback.answer("⏳")
    await _apply_pack(callback.message, t, state, warehouse, float(max(1, callback_data.i)))


@router.message(WhSt.ed_pack, F.text)
async def edit_pack_text(message: Message, t: Dict[str, str], state: FSMContext,
                         warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _pack_error(t, message.text, value)
    if error:
        await message.answer(error, reply_markup=kb.pack_kb(t, "epk"))
        return
    await _apply_pack(message, t, state, warehouse, value)


# ---- 📥 Kirim davomida: karobkadagi miqdorni shu yerning o'zida o'zgartirish ----
@router.callback_query(StateFilter(WhSt.mode, WhSt.qty), WhCb.filter(F.a == "chpk"))
async def kirim_change_pack(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    data = await state.get_data()
    cur = data.get("cur") or {}
    if not cur or data.get("flow") != FLOW_IN:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await state.set_state(WhSt.kir_pack)
    await state.update_data(kp_val=None)
    await _show(callback.message, t["wh_kp_prompt"].format(name=escape(cur["name"]), pack=_n(cur.get("pack") or 1),
                                                           unit=escape(cur["unit"])) + "\n\n" + t["wh_pack_why"],
                kb.pack_kb(t, "kpk"), True)


async def _kirim_pack_scope(target: Message, t: Dict[str, str], state: FSMContext, value: float,
                            edit: bool) -> None:
    cur = (await state.get_data()).get("cur") or {}
    await state.update_data(kp_val=value)
    await _show(target, t["wh_kp_scope"].format(name=escape(cur.get("name", "")), pack=_n(value),
                                                unit=escape(cur.get("unit", ""))), kb.pack_scope_kb(t), edit)


@router.callback_query(WhSt.kir_pack, WhCb.filter(F.a == "kpk"))
async def kirim_pack_preset(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                            state: FSMContext) -> None:
    await callback.answer()
    await _kirim_pack_scope(callback.message, t, state, float(max(1, callback_data.i)), edit=True)


@router.message(WhSt.kir_pack, F.text)
async def kirim_pack_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    value = parse_number(message.text)
    error = _pack_error(t, message.text, value)
    if error:
        await message.answer(error, reply_markup=kb.pack_kb(t, "kpk"))
        return
    await _kirim_pack_scope(message, t, state, value, edit=False)


@router.callback_query(WhSt.kir_pack, WhCb.filter(F.a == "kps"))
async def kirim_pack_apply(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                           warehouse: WarehouseService) -> None:
    data = await state.get_data()
    cur, value = dict(data.get("cur") or {}), data.get("kp_val")
    if not cur or not value:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    if callback_data.k == "save":
        try:
            await warehouse.update_product(cur["name"], pack=value)     # mahsulotning standart qiymati ham
        except Exception:
            logger.exception("Karobkadagi miqdorni saqlab bo'lmadi")
            await callback.answer(t["wh_save_error"], show_alert=True)
            return
    await callback.answer(t["wh_kp_saved"] if callback_data.k == "save" else t["wh_kp_once"])
    cur["pack"] = value
    cur["mode"] = "unit" if value <= 1 else cur.get("mode", "unit")
    await state.update_data(cur=cur, kp_val=None)
    if value > 1:
        await _show_mode(callback.message, t, state, edit=True)      # karobka / dona qayta tanlanadi
    else:
        await _ask_qty(callback.message, t, state, edit=True)


# ---------------- 💰 narxni belgilash / o'zgartirish ----------------
async def _ask_edit_price(target: Message, t: Dict[str, str], state: FSMContext, product: Product,
                          edit: bool, warehouse: Optional[WarehouseService] = None) -> None:
    await state.set_state(WhSt.ed_price)
    await state.update_data(ed_name=product.name)
    current = _n(product.avg_price) if product.avg_price else "—"
    last = None
    if warehouse is not None:
        try:
            warehouse.invalidate()                         # «Приход» qayta o'qiladi: eng pastdagi (oxirgi) narx
            last = await warehouse.last_price(product.name)
        except Exception:
            logger.warning("Oxirgi narxni o'qib bo'lmadi", exc_info=True)
    await _show(target, t["wh_setprice_prompt"].format(name=escape(product.name), unit=escape(product.unit),
                                                       price=current, last=_n(last) if last else "—"),
                kb.cancel_kb(t), edit)


@router.message(WhSt.ed_price, F.text)
async def edit_price_text(message: Message, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    error = _number_error(t, message.text, value, "price")
    if error:
        await message.answer(error, reply_markup=kb.cancel_kb(t))
        return
    name = (await state.get_data()).get("ed_name", "")
    try:
        res = await warehouse.set_product_price(name, value)
    except NotFoundError:
        await message.answer(t["wh_not_in_catalog"])
        return
    except Exception:
        logger.exception("Narxni yozib bo'lmadi")
        await message.answer(t["wh_save_error"])
        return
    if res.get("no_history"):
        await message.answer(t["wh_setprice_nohist"].format(name=escape(name), price=_n(value)))
    else:
        if res.get("was_unpriced"):
            await _resolve_unpriced(warehouse, {"name": res["name"], "qty": res["qty"],
                                                "iso": res["day"].isoformat() if res.get("day") else ""})
        await message.answer(t["wh_setprice_done"].format(
            name=escape(name), price=_n(value), date=res["day"].strftime("%d.%m.%Y") if res.get("day") else "—",
            qty=_n(res["qty"]), unit=escape(res.get("unit") or ""),
            old=_n(res["old_price"]) if res.get("old_price") else "—",
            avg=_n(res["avg"]) if res.get("avg") is not None else "—", count=res.get("count", 1)))
    await _show_edit_card(message, t, state, warehouse, name, edit=False)


# ---------------- ⚖️ qoldiqni to'g'rilash (inventarizatsiya) ----------------
async def _ask_adj(target: Message, t: Dict[str, str], state: FSMContext, product: Product, edit: bool) -> None:
    await state.set_state(WhSt.ed_adj)
    await state.update_data(ed_name=product.name)
    await _show(target, t["wh_adj_prompt"].format(name=escape(product.name), stock=_n(product.stock),
                                                  unit=escape(product.unit)), kb.cancel_kb(t), edit)


@router.message(WhSt.ed_adj, F.text)
async def adjust_text(message: Message, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    """+N — «Приход» ga tuzatish qatori, -N — «Расход» ga (qoldiq manfiy bo'lib qolmaydi)."""
    raw = (message.text or "").strip()
    value = parse_number(raw)
    if value is None:
        await message.answer(t["wh_adj_nan"].format(raw=escape(raw[:30])), reply_markup=kb.cancel_kb(t))
        return
    if abs(value) < 1e-9:
        await message.answer(t["wh_adj_zero"], reply_markup=kb.cancel_kb(t))
        return
    if abs(value) > 10**9:
        await message.answer(t["wh_adj_big"], reply_markup=kb.cancel_kb(t))
        return
    name = (await state.get_data()).get("ed_name", "")
    try:
        product = await warehouse.product(name, force=True)          # Google Sheets dagi eng so'nggi qoldiq
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await message.answer(t["error"])
        return
    if product is None:
        await message.answer(t["wh_not_in_catalog"])
        return
    if product.stock + value < -1e-9:
        await message.answer(t["wh_adj_negative"].format(stock=_n(product.stock), unit=escape(product.unit)),
                             reply_markup=kb.cancel_kb(t))
        return
    try:
        if value > 0:
            # o'rtacha narx bilan: «Средняя цена» o'zgarmaydi, «Сумма остатка» to'g'ri qoladi
            await warehouse.save_inflow([InflowItem(name=product.name, unit=product.unit, qty=value,
                                                    price=product.avg_price or None, supplier=ADJ_IN_NOTE)])
            sheet, note = warehouse.inflow_tab, ADJ_IN_NOTE
        else:
            await warehouse.save_outflow([OutflowItem(name=product.name, unit=product.unit, qty=-value, purpose="",
                                                      recipient=ADJ_OUT_NOTE, stock_before=product.stock)])
            sheet, note = warehouse.outflow_tab, ADJ_OUT_NOTE
    except Exception:
        logger.exception("Qoldiqni to'g'rilab bo'lmadi")
        await message.answer(t["wh_save_error"])
        return
    try:
        after = await warehouse.product(product.name, force=True)
    except Exception:
        after = None
    new_stock = after.stock if after is not None else product.stock + value
    delta = ("+" if value > 0 else "−") + _n(abs(value))
    await message.answer(t["wh_adj_done"].format(name=escape(product.name), old=_n(product.stock), new=_n(new_stock),
                                                 unit=escape(product.unit), delta=delta, sheet=escape(sheet),
                                                 note=escape(note)))
    await _show_edit_card(message, t, state, warehouse, product.name, edit=False)


# ---------------- 🎞 Кромка ПВХ: o'lcham -> mavjud rang (faqat tugma) -> ➕ yangi kromka ----------------
async def _krm_products(warehouse: WarehouseService, size: str, force: bool = False) -> List[Product]:
    items = [p for p in await warehouse.catalog(force) if kromka.matches_size(p.name, size)]
    return sorted(items, key=lambda p: kromka.color_of(p.name).casefold())


def _krm_labels(t: Dict[str, str], items: List[Product], size: str) -> List[str]:
    return [t["wh_krm_color_btn"].format(color=kromka.color_of(p.name) or "—", size=size, stock=_n(p.stock),
                                         unit=p.unit) for p in items]


async def _krm_sizes(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    await state.set_state(WhSt.krm_size)
    await state.update_data(view="pick")
    await _show(target, t["wh_krm_size"], kb.kromka_size_kb(t, kromka.SIZES), edit)


async def _krm_colors(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                      size: str, edit: bool) -> None:
    try:
        items = await _krm_products(warehouse, size)
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    names = [p.name for p in items]
    await state.set_state(WhSt.krm_color)
    await state.update_data(krm_size=size, krm_opts=names, opts=names, view="pick")
    text = t["wh_krm_colors" if items else "wh_krm_colors_empty"].format(size=size)
    await _show(target, text, kb.kromka_colors_kb(t, _krm_labels(t, items, size), size), edit)


async def _krm_new_prompt(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                          edit: bool) -> None:
    size = (await state.get_data()).get("krm_size") or kromka.SIZES[0]
    try:
        items = await _krm_products(warehouse, size)
    except Exception:
        items = []
    await state.set_state(WhSt.krm_new)
    await state.update_data(krm_opts=[p.name for p in items])
    text = t["wh_krm_new_prompt"].format(size=size) + (t["wh_krm_new_ref"] if items else "")
    await _show(target, text, kb.kromka_ref_kb(t, _krm_labels(t, items, size)), edit)


async def _krm_route(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                     text: str, edit: bool) -> None:
    """Matnda o'lcham bo'lsa — darhol ranglar, bo'lmasa — o'lcham tanlovi."""
    size = kromka.parse_size(text)
    if size:
        await _krm_colors(target, t, state, warehouse, size, edit)
    else:
        await _krm_sizes(target, t, state, edit)


@router.callback_query(StateFilter(WhSt), WhCb.filter(F.a == "krm"))
async def kromka_open(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    if (await state.get_data()).get("flow") not in (FLOW_IN, FLOW_OUT):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _krm_sizes(callback.message, t, state, edit=True)


@router.callback_query(WhSt.krm_size, WhCb.filter(F.a == "krs"))
async def kromka_size(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    if callback_data.i >= len(kromka.SIZES):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await _krm_colors(callback.message, t, state, warehouse, kromka.SIZES[callback_data.i], edit=True)


@router.message(WhSt.krm_size, F.text)
async def kromka_size_text(message: Message, t: Dict[str, str], state: FSMContext,
                           warehouse: WarehouseService) -> None:
    size = kromka.parse_size(message.text)
    if size:
        await _krm_colors(message, t, state, warehouse, size, edit=False)
        return
    await message.answer(t["wh_krm_size_hint"], reply_markup=kb.kromka_size_kb(t, kromka.SIZES))


@router.callback_query(StateFilter(WhSt.krm_color, WhSt.krm_new), WhCb.filter(F.a == "krc"))
async def kromka_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str], state: FSMContext,
                      warehouse: WarehouseService) -> None:
    opts = (await state.get_data()).get("krm_opts") or []
    if callback_data.i >= len(opts):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    await state.update_data(opts=opts)
    await _pick(callback.message, t, state, warehouse, opts[callback_data.i], edit=True)


@router.message(WhSt.krm_color, F.text)
async def kromka_color_text(message: Message, t: Dict[str, str], state: FSMContext,
                            warehouse: WarehouseService) -> None:
    """Rangni yozib bo'lmaydi: faqat mavjud rang tugmasi yoki «➕ Yangi kromka qo'shish»."""
    size = (await state.get_data()).get("krm_size") or kromka.SIZES[0]
    await message.answer(t["wh_krm_strict"])
    await _krm_colors(message, t, state, warehouse, size, edit=False)


@router.callback_query(WhSt.krm_color, WhCb.filter(F.a == "krn"))
async def kromka_new(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                     warehouse: WarehouseService) -> None:
    await callback.answer()
    await _krm_new_prompt(callback.message, t, state, warehouse, edit=True)


@router.message(WhSt.krm_new, F.text)
async def kromka_new_name(message: Message, t: Dict[str, str], state: FSMContext,
                          warehouse: WarehouseService) -> None:
    data = await state.get_data()
    size = data.get("krm_size") or kromka.SIZES[0]
    color = kromka.clean_color(clean_text(message.text, 60))
    try:
        catalog = await warehouse.catalog(force=True)
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi")
        await message.answer(t["error"])
        return
    items = sorted((p for p in catalog if kromka.matches_size(p.name, size)),
                   key=lambda p: kromka.color_of(p.name).casefold())
    await state.update_data(krm_opts=[p.name for p in items])
    ref = kb.kromka_ref_kb(t, _krm_labels(t, items, size))
    if not color:
        await message.answer(t["wh_krm_bad_name"], reply_markup=ref)
        return
    name = kromka.build_name(color, size)
    dup = exact_match(name, [p.name for p in catalog]) or next(
        (p.name for p in items if normalize(kromka.color_of(p.name)) == normalize(color)), None)
    if dup:
        await message.answer(t["wh_krm_dup"].format(name=escape(dup)), reply_markup=ref)
        return
    try:
        await warehouse.add_category(kromka.CATEGORY)
    except Exception:
        logger.warning("Kategoriyani qo'shib bo'lmadi", exc_info=True)
    flow = data.get("flow")
    # kategoriya / birlik / karobka avtomatik: faqat Мин. норматив so'raladi, keyin tasdiqlash
    await state.update_data(np={"ret": flow if flow in (FLOW_IN, FLOW_OUT) else None, "name": name,
                                "cat": kromka.CATEGORY, "unit": kromka.UNIT, "pack": 1.0, "krm": size})
    await _np_ask_min(message, t, state, 1.0, edit=False, warehouse=warehouse)


# ---------------- eskirgan tugmalar ----------------
@router.callback_query(WhCb.filter())
async def expired(callback: CallbackQuery, t: Dict[str, str]) -> None:
    await callback.answer(t["wh_expired"], show_alert=True)
