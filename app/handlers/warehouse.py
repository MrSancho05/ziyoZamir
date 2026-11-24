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
from app.services.flows import FlowsService
from app.services.categories import examples as cat_examples
from app.services.categories import label as cat_label
from app.services.categories import suggest as suggest_category
from app.services.search import exact_match, normalize, search_products
from app.services.warehouse import (
    RECIPIENT,
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
    await _show(target, text, kb.categories_kb(t, labels, len(data.get("cart", []))), edit)


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
    cats = (await state.get_data()).get("cats", [])
    if callback_data.i >= len(cats):
        await callback.answer(t["wh_expired"], show_alert=True)
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
        text = (t["wh_did_you_mean"] if hits else t["wh_not_found"]).format(query=escape(query))
    await state.update_data(opts=hits)
    await message.answer(text, reply_markup=kb.search_kb(t, hits, allow_new))


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
        await state.update_data(rn={"old": product.name}, view="item")
        await state.set_state(WhSt.rn_name)
        await _show(target, t["wh_rn_prompt"].format(name=escape(product.name)), kb.cancel_kb(t), edit)
    elif flow == FLOW_DEL:
        await state.update_data(act={"kind": "del", "name": product.name}, view="item")
        await state.set_state(WhSt.confirm)
        await _show(target, t["wh_del_confirm"].format(name=escape(product.name)),
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
    if flow == FLOW_IN and product.pack > 1:
        await state.set_state(WhSt.mode)
        await _show(target, t["wh_mode_prompt"].format(name=escape(product.name), pack=_n(product.pack),
                                                       unit=escape(product.unit)),
                    kb.mode_kb(t, _n(product.pack), product.unit), edit)
        return
    await _ask_qty(target, t, state, edit)


async def _ask_qty(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    data = await state.get_data()
    cur = data["cur"]
    unit = t["wh_unit_box"] if cur.get("mode") == "pack" else cur["unit"]
    await state.set_state(WhSt.qty)
    if data.get("flow") == FLOW_OUT:
        # savatda shu mahsulotdan oldin olingani ham hisobga olinadi
        taken = sum(it["qty"] for it in data.get("cart", []) if it["name"] == cur["name"])
        text = t["wh_qty_prompt_out"].format(name=escape(cur["name"]), stock=_n(cur["stock"] - taken),
                                             unit=escape(unit))
    else:
        text = t["wh_qty_prompt_in"].format(name=escape(cur["name"]), unit=escape(unit))
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
    if value is None or value <= 0 or value > 10**9:
        await message.answer(t["wh_bad_number"], reply_markup=kb.cancel_kb(t))
        return
    data = await state.get_data()
    cur = dict(data["cur"])
    cur["qty_in"] = value
    await state.update_data(cur=cur)
    if data.get("flow") == FLOW_IN:
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
        await message.answer(t["wh_price_prompt"].format(unit=escape(unit)),
                             reply_markup=kb.price_kb(t, _n(last) if last else None))
    else:
        cur["qty"] = value
        await state.update_data(cur=cur)
        await _ask_who(message, t, state, warehouse, edit=False)


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
    if value is None or value <= 0 or value > 10**13:
        await message.answer(t["wh_bad_number"], reply_markup=kb.cancel_kb(t))
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
            await warehouse.save_inflow(items)
            tab = warehouse.inflow_tab
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
    await state.update_data(np_cats=cats)
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
    await state.set_state(WhSt.np_unit)
    await _show(target, t["wh_np_unit"], kb.np_unit_kb(t, UNITS), edit)


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
    await _show(target, t["wh_np_pack"].format(unit=escape(unit)), kb.quick_value_kb(t, "pk1", "1"), edit)


@router.callback_query(WhSt.np_unit, WhCb.filter(F.a == "unit"))
async def np_unit_pick(callback: CallbackQuery, callback_data: WhCb, t: Dict[str, str],
                       state: FSMContext) -> None:
    unit = UNITS[callback_data.i] if callback_data.i < len(UNITS) else UNITS[0]
    await _np_ask_pack(callback.message, t, state, unit, edit=True)
    await callback.answer()


@router.message(WhSt.np_unit, F.text)
async def np_unit_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    unit = clean_text(message.text, 15)
    if unit:
        await _np_ask_pack(message, t, state, unit, edit=False)


async def _np_ask_min(target: Message, t: Dict[str, str], state: FSMContext, pack: float, edit: bool) -> None:
    await _np_update(state, pack=pack)
    await state.set_state(WhSt.np_min)
    await _show(target, t["wh_np_min"], kb.quick_value_kb(t, "mn0", "0"), edit)


@router.callback_query(WhSt.np_pack, WhCb.filter(F.a == "pk1"))
async def np_pack_default(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await _np_ask_min(callback.message, t, state, 1.0, edit=True)
    await callback.answer()


@router.message(WhSt.np_pack, F.text)
async def np_pack_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    value = parse_number(message.text)
    if value is None or value <= 0 or value > 10**6:
        await message.answer(t["wh_bad_number"], reply_markup=kb.quick_value_kb(t, "pk1", "1"))
        return
    await _np_ask_min(message, t, state, value, edit=False)


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
    await _np_confirm(callback.message, t, state, 0.0, edit=True)
    await callback.answer()


@router.message(WhSt.np_min, F.text)
async def np_min_text(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    value = parse_number(message.text)
    if value is None or value < 0 or value > 10**9:
        await message.answer(t["wh_bad_number"], reply_markup=kb.quick_value_kb(t, "mn0", "0"))
        return
    await _np_confirm(message, t, state, value, edit=False)


@router.callback_query(WhSt.np_confirm, WhCb.filter(F.a == "ok"))
async def np_save(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                  warehouse: WarehouseService) -> None:
    np = (await state.get_data()).get("np") or {}
    if not np.get("name"):
        await callback.answer(t["wh_expired"], show_alert=True)
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
    saved = t["wh_np_saved"].format(name=escape(np["name"]), row=row)
    ret = np.get("ret")
    if ret in (FLOW_IN, FLOW_OUT):
        # Kirim/Chiqim davomida qo'shilgan: shu mahsulot bilan darhol davom etamiz
        await state.update_data(flow=ret, np=None)
        await _show(callback.message, saved, None, True)
        try:
            product = await warehouse.product(np["name"], force=True)
        except Exception:
            product = None
        if product is not None:
            await _start_item(callback.message, t, state, product, edit=False)
            return
        await _show_cart(callback.message, t, state, edit=False)
        return
    await state.clear()
    await _show(callback.message, saved, None, True)


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
            text = t["wh_rn_done"].format(new=escape(act["new"]), count=sum(changed.values()))
        elif act.get("kind") == "del":
            await warehouse.delete_product(act["name"])
            text = t["wh_del_done"].format(name=escape(act["name"]))
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
    if not rows:
        await _show(target, t["wh_unpriced_none"], None, edit)
        return
    rows = rows[-20:]                                  # eng oxirgi 20 tasi
    await state.update_data(unp=[{"row": r.row, "name": r.name, "unit": r.unit, "qty": r.qty,
                                  "day": r.day.strftime("%d.%m.%Y") if r.day else "—",
                                  "sup": r.supplier} for r in rows], view="unp")
    labels = [f"{r.name} · {_n(r.qty)} {r.unit}" for r in rows]
    await _show(target, t["wh_unpriced_title"].format(count=len(rows)), kb.unpriced_kb(t, labels), edit)


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
    await target.answer(t["wh_unpriced_saved"].format(name=escape(r["name"]), price=_n(price), qty=_n(r["qty"]),
                                                      total=_n(total)))
    await _show_unpriced(target, t, state, warehouse, edit=False)


@router.message(WhSt.unp_price, F.text)
async def unpriced_price_text(message: Message, t: Dict[str, str], state: FSMContext,
                              warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    if value is None or value <= 0 or value > 10**13:
        await message.answer(t["wh_bad_price"], reply_markup=kb.cancel_kb(t))
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


# ---------------- eskirgan tugmalar ----------------
@router.callback_query(WhCb.filter())
async def expired(callback: CallbackQuery, t: Dict[str, str]) -> None:
    await callback.answer(t["wh_expired"], show_alert=True)
