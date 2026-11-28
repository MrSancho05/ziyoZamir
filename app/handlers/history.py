"""🗂 Yozuvlarni tahrirlash / o'chirish (menyu orqali): 📦 Mahsulotlar -> 🗂 Yozuvlarni tahrirlash.

Bugungi va o'tgan kunlardagi 📥 Kirim, 📤 Chiqim, 🧾 Boshqa xarajat qatorlari (miqdor / narx / summa, o'chirish)
va 🏪 do'kon kunini tozalash. AI yordamchi bilan BIR XIL qoralama va «✅ Tasdiqlash» kartasi ishlatiladi
(`ai_confirm.execute`): Table qatori `deleteDimension` bilan o'chadi, formulalar qayta hisoblaydi, keshlar tozalanadi.
Faqat Admin va Sklad nazoratchisi (MANAGE_WAREHOUSE)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from html import escape
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.handlers.ai_confirm import confirm_kb
from app.keyboards.callbacks import HiCb, WhCb
from app.locales.texts import menu_texts
from app.models import User
from app.services.ai_agent import AIAgent, Ctx
from app.services.warehouse import parse_number

logger = logging.getLogger(__name__)
router = Router(name="history")
router.message.filter(HasPermission(Permission.MANAGE_WAREHOUSE))
router.callback_query.filter(HasPermission(Permission.MANAGE_WAREHOUSE))

KINDS = {"in": "inflow", "out": "outflow", "other": "other"}
_builder: Optional[AIAgent] = None


class HiSt(StatesGroup):
    search = State()        # mahsulot nomi yoki sana kutilmoqda
    value = State()         # yangi miqdor / narx / summa
    shop_date = State()     # 🏪 tozalanadigan kun
    shop_reason = State()   # 🏪 Bossga yuborilgan kun: sabab


def _n(x: Any) -> str:
    s = f"{round(float(x or 0), 2):,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


def _b(text: str, a: str, k: str = "", i: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=HiCb(a=a, k=k, i=i).pack())


def _nav(t: Dict[str, str], back: InlineKeyboardButton) -> List[InlineKeyboardButton]:
    return [back, InlineKeyboardButton(text=t["wh_btn_cancel"], callback_data=WhCb(a="cancel").pack())]


def _builder_for(warehouse, shop, pending, settings) -> AIAgent:
    """Qoralama yasovchi (Gemini chaqirmaydi): AI bilan bir xil kartalar va tekshiruvlar."""
    global _builder
    if _builder is None or _builder._wh is not warehouse:
        _builder = AIAgent("", "", settings.timezone, warehouse=warehouse, shop=shop, pending=pending)
    return _builder


def _parse_date(text: str) -> Optional[date]:
    s = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


async def _send(target: Message, text: str, markup: Optional[InlineKeyboardMarkup], edit: bool) -> None:
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await target.answer(text, reply_markup=markup)


def _kind_label(t: Dict[str, str], k: str) -> str:
    return {"in": t["hi_btn_in"], "out": t["hi_btn_out"], "other": t["hi_btn_other"], "shop": t["hi_btn_shop"]}[k]


@router.callback_query(HiCb.filter(F.a == "menu"))
async def open_menu(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_b(t["hi_btn_in"], "kind", "in"), _b(t["hi_btn_out"], "kind", "out")],
        [_b(t["hi_btn_other"], "kind", "other"), _b(t["hi_btn_shop"], "kind", "shop")],
        _nav(t, InlineKeyboardButton(text=t["wh_btn_back"], callback_data=WhCb(a="pmm").pack()))])
    await _send(callback.message, t["hi_pick_kind"], kb, True)


async def _rows(kind: str, warehouse, query: str = "", day: Optional[date] = None) -> List[Dict[str, Any]]:
    if kind in ("in", "out"):
        entries = await warehouse.flow_entries(kind, query, day, limit=10)
        return [{"row": e.row, "date": e.day.strftime("%d.%m.%Y") if e.day else "—", "name": e.name, "qty": e.qty,
                 "unit": e.unit, "price": e.price, "party": e.party, "purpose": e.purpose} for e in entries]
    other = getattr(warehouse, "other", None)
    if other is None:
        return []
    rows = await other.rows(force=True)
    if day is not None:
        rows = [r for r in rows if r.day == day]
    if query:
        q = query.casefold()
        rows = [r for r in rows if q in r.description.casefold() or q in r.category.casefold()]
    rows = sorted(rows, key=lambda r: (r.day, r.row), reverse=True)[:10]
    return [{"row": r.row, "date": r.day.strftime("%d.%m.%Y"), "cat": r.category, "desc": r.description,
             "amount": r.amount} for r in rows]


async def _show_list(target: Message, t: Dict[str, str], state: FSMContext, warehouse, kind: str, query: str = "",
                     day: Optional[date] = None, edit: bool = True) -> None:
    try:
        rows = await _rows(kind, warehouse, query, day)
    except Exception:
        logger.exception("Yozuvlarni o'qib bo'lmadi")
        await target.answer(t["error"])
        return
    await state.set_state(HiSt.search)
    await state.update_data(hi_kind=kind, hi_rows=rows)
    flt = ""
    if query or day:
        flt = " · " + escape(" ".join(x for x in (query, day.strftime("%d.%m.%Y") if day else "") if x))
    label = _kind_label(t, kind)
    if not rows:
        text = t["hi_empty"].format(kind=label, filter=flt)
    else:
        text = t["hi_list"].format(kind=label, filter=flt)
    buttons = []
    for i, r in enumerate(rows):
        if kind == "other":
            txt = f"{r['date'][:5]} · {r['desc'][:18]} · {_n(r['amount'])}"
        else:
            txt = f"{r['date'][:5]} · {r['name'][:18]} · {_n(r['qty'])}"
        buttons.append([_b(txt, "row", kind, i)])
    buttons.append(_nav(t, _b(t["wh_btn_back"], "menu")))
    await _send(target, text, InlineKeyboardMarkup(inline_keyboard=buttons), edit)


@router.callback_query(HiCb.filter(F.a == "kind"))
async def pick_kind(callback: CallbackQuery, callback_data: HiCb, t: Dict[str, str], state: FSMContext,
                    warehouse=None) -> None:
    await callback.answer()
    if callback_data.k == "shop":
        await state.set_state(HiSt.shop_date)
        await _send(callback.message, t["hi_shop_ask_date"],
                    InlineKeyboardMarkup(inline_keyboard=[_nav(t, _b(t["wh_btn_back"], "menu"))]), True)
        return
    await _show_list(callback.message, t, state, warehouse, callback_data.k)


@router.message(HiSt.search, F.text, ~F.text.in_(menu_texts()))
async def search_text(message: Message, t: Dict[str, str], state: FSMContext, warehouse=None) -> None:
    kind = (await state.get_data()).get("hi_kind", "in")
    text = (message.text or "").strip()
    day = _parse_date(text)
    await _show_list(message, t, state, warehouse, kind, "" if day else text[:60], day, edit=False)


@router.callback_query(HiCb.filter(F.a == "row"))
async def pick_row(callback: CallbackQuery, callback_data: HiCb, t: Dict[str, str], state: FSMContext) -> None:
    data = await state.get_data()
    rows = data.get("hi_rows") or []
    kind = data.get("hi_kind") or callback_data.k
    if callback_data.i >= len(rows):
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    r = rows[callback_data.i]
    await state.update_data(hi_sel=r)
    cur = t["es_cur"]
    if kind == "in":
        text = t["hi_card_in"].format(row=r["row"], date=r["date"], name=escape(r["name"]), qty=_n(r["qty"]),
                                      unit=escape(r["unit"]), party=escape(r["party"] or "—"),
                                      price=f"{_n(r['price'])} {cur}" if r.get("price") else t["wh_price_later_mark"])
        acts = [_b(t["hi_btn_qty"], "ed", "qty"), _b(t["hi_btn_price"], "ed", "price")]
    elif kind == "out":
        text = t["hi_card_out"].format(row=r["row"], date=r["date"], name=escape(r["name"]), qty=_n(r["qty"]),
                                       party=escape(r["party"] or "—"), purpose=escape(r["purpose"] or "—"))
        acts = [_b(t["hi_btn_qty"], "ed", "qty")]
    else:
        text = t["hi_card_other"].format(row=r["row"], date=r["date"], cat=escape(r["cat"]), desc=escape(r["desc"]),
                                         amount=_n(r["amount"]))
        acts = [_b(t["hi_btn_amount"], "ed", "amount")]
    kb = InlineKeyboardMarkup(inline_keyboard=[acts + [_b(t["hi_btn_delete"], "del")],
                                               _nav(t, _b(t["wh_btn_back"], "kind", kind))])
    await _send(callback.message, text, kb, True)


async def _deliver(target: Message, t: Dict[str, str], state: FSMContext, ctx: Ctx, res: Dict[str, Any]) -> None:
    await state.set_state(None)
    if ctx.drafts:
        d = ctx.drafts[-1]
        d.source = "menu"
        await target.answer(d.card, reply_markup=confirm_kb(t, d.id))
        return
    err = str(res.get("error") or "")
    if res.get("message"):
        await target.answer(res["message"])
    elif "insufficient" in err:
        await target.answer(t["ai_fail_stock"].format(stock=_n(res.get("stock")), unit=""))
    elif "not found" in err or "moved" in err:
        await target.answer(t["ai_fail_moved"])
    else:
        await target.answer(t["ai_fail_generic"])


def _ctx(user: User, t: Dict[str, str]) -> Ctx:
    return Ctx(user.id, user.role, t.get("lang_code", "uz"), user.full_name)


@router.callback_query(HiCb.filter(F.a == "del"))
async def delete_row(callback: CallbackQuery, t: Dict[str, str], user: User, state: FSMContext, warehouse=None,
                     shop=None, pending=None, settings=None) -> None:
    data = await state.get_data()
    r, kind = data.get("hi_sel"), data.get("hi_kind")
    if not r or kind not in KINDS:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    b = _builder_for(warehouse, shop, pending, settings)
    ctx = _ctx(user, t)
    res = await b.run_tool("draft_delete_entry", {"kind": KINDS[kind], "row": r["row"]}, ctx, trusted=True)
    await _deliver(callback.message, t, state, ctx, res)


@router.callback_query(HiCb.filter(F.a == "ed"))
async def edit_prompt(callback: CallbackQuery, callback_data: HiCb, t: Dict[str, str], state: FSMContext) -> None:
    r = (await state.get_data()).get("hi_sel")
    if not r:
        await callback.answer(t["wh_expired"], show_alert=True)
        return
    await callback.answer()
    field = callback_data.k
    old = {"qty": r.get("qty"), "price": r.get("price"), "amount": r.get("amount")}.get(field)
    await state.update_data(hi_field=field)
    await state.set_state(HiSt.value)
    await callback.message.answer(t[f"hi_ask_{field}"].format(old=_n(old) if old else "—"),
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                      _nav(t, _b(t["wh_btn_back"], "kind", (await state.get_data()).get("hi_kind", "in")))]))


@router.message(HiSt.value, F.text, ~F.text.in_(menu_texts()))
async def edit_value(message: Message, t: Dict[str, str], user: User, state: FSMContext, warehouse=None,
                     shop=None, pending=None, settings=None) -> None:
    data = await state.get_data()
    r, kind, field = data.get("hi_sel"), data.get("hi_kind"), data.get("hi_field")
    if not r or kind not in KINDS:
        await message.answer(t["wh_expired"])
        return
    value = parse_number(message.text)
    if value is None or value < 0 or (value == 0 and field != "price"):
        await message.answer(t["hi_bad_number"])
        return
    args: Dict[str, Any] = {"kind": KINDS[kind], "row": r["row"]}
    if field == "qty":
        args["quantity"] = value
    elif field == "price":
        args["price_per_unit"] = value
    else:
        args["amount"] = message.text.strip()
    b = _builder_for(warehouse, shop, pending, settings)
    ctx = _ctx(user, t)
    res = await b.run_tool("draft_edit_entry", args, ctx, trusted=True)
    await _deliver(message, t, state, ctx, res)


@router.message(HiSt.shop_date, F.text, ~F.text.in_(menu_texts()))
async def shop_date(message: Message, t: Dict[str, str], user: User, state: FSMContext, warehouse=None,
                    shop=None, pending=None, settings=None) -> None:
    day = _parse_date(message.text)
    if day is None:
        await message.answer(t["hi_bad_date"])
        return
    found = await shop.get_day(day)
    if not (found and found[0]):
        await message.answer(t["hi_shop_none"].format(date=day.strftime("%d.%m.%Y")))
        return
    if pending is not None and await pending.is_sent("shop", day.isoformat()) is True:
        await state.update_data(hi_shop=day.isoformat())
        await state.set_state(HiSt.shop_reason)
        await message.answer(t["shop_reason_prompt"].format(date=day.strftime("%d.%m.%Y"), old=_n(found[1]), new="0"))
        return
    b = _builder_for(warehouse, shop, pending, settings)
    ctx = _ctx(user, t)
    res = await b.run_tool("draft_delete_entry", {"kind": "shop", "date": day.isoformat()}, ctx, trusted=True)
    await _deliver(message, t, state, ctx, res)


@router.message(HiSt.shop_reason, F.text, ~F.text.in_(menu_texts()))
async def shop_reason(message: Message, t: Dict[str, str], user: User, state: FSMContext, warehouse=None,
                      shop=None, pending=None, settings=None) -> None:
    iso = (await state.get_data()).get("hi_shop")
    if not iso:
        await message.answer(t["wh_expired"])
        return
    b = _builder_for(warehouse, shop, pending, settings)
    ctx = _ctx(user, t)
    res = await b.run_tool("draft_delete_entry", {"kind": "shop", "date": iso, "reason": message.text[:200]}, ctx, trusted=True)
    await _deliver(message, t, state, ctx, res)
