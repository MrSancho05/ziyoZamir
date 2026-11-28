"""🧾 Boshqa xarajatlar (ombor tovarlariga aloqasiz): benzin, xona/ofis, transport, ta'mirlash ...

Faqat sklad nazoratchisi va admin. Oqim: kategoriya -> izoh -> summa -> «Прочие расходы» varag'iga yoziladi.
Har qadamda ⬅️ Orqaga va ❌ Bekor qilish."""
from __future__ import annotations

import logging
from html import escape
from typing import Dict, List

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.locales.texts import all_texts, menu_texts
from app.models import User
from app.services.other_expenses import DEFAULT_CATEGORIES, category_label
from app.services.payroll import fmt_money
from app.services.warehouse import WarehouseService, clean_text, parse_number

logger = logging.getLogger(__name__)
router = Router(name="other_expenses")
router.message.filter(HasPermission(Permission.MANAGE_WAREHOUSE))
router.callback_query.filter(HasPermission(Permission.MANAGE_WAREHOUSE))


class ExCb(CallbackData, prefix="ox"):
    a: str
    i: int = 0


class ExSt(StatesGroup):
    cat = State()
    desc = State()
    amount = State()


def _b(text: str, a: str, i: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=ExCb(a=a, i=i).pack())


def _nav(t: Dict[str, str], back: bool = True) -> List[InlineKeyboardButton]:
    return ([_b(t["wh_btn_step_back"], "back")] if back else []) + [_b(t["wh_btn_cancel"], "cancel")]


def _kb(buttons: List[InlineKeyboardButton], nav: List[List[InlineKeyboardButton]], cols: int = 2) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in buttons:
        kb.add(b)
    kb.adjust(cols)
    for row in nav:
        kb.row(*row)
    return kb.as_markup()


async def _show(target: Message, text: str, markup, edit: bool) -> None:
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await target.answer(text, reply_markup=markup)


def _lang(t: Dict[str, str]) -> str:
    return t.get("lang_code", "uz")


@router.message(StateFilter(ExSt), F.text.in_(menu_texts()) | F.text.startswith("/"))
async def leave_on_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    raise SkipHandler()


async def _menu(target: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService,
                edit: bool) -> None:
    await state.clear()
    lines = [t["ox_title"]]
    if warehouse.other is not None:
        try:
            today = warehouse.today()
            s = await warehouse.other.summary(today, today)
            lines += ["", t["ox_today"].format(total=fmt_money(int(round(s.total))), n=s.count)]
            lines += [f"• {escape(category_label(r.category, _lang(t)), quote=False)} — {fmt_money(int(round(r.amount)))} · "
                      f"{escape(r.description)}" for r in s.rows[-10:]]
        except Exception:
            logger.exception("«Прочие расходы» ni o'qib bo'lmadi")
    lines += ["", t["ox_hint"]]
    await _show(target, "\n".join(lines), _kb([_b(t["ox_btn_add"], "add")], [_nav(t, back=False)]), edit)


@router.message(F.text.in_(all_texts("btn_other_exp")))
async def open_menu(message: Message, t: Dict[str, str], state: FSMContext, warehouse: WarehouseService) -> None:
    await _menu(message, t, state, warehouse, edit=False)


async def _ask_cat(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    await state.set_state(ExSt.cat)
    lang = _lang(t)
    buttons = [_b(category_label(c, lang), "cat", i) for i, (c, _uz, _ru) in enumerate(DEFAULT_CATEGORIES)]
    await _show(target, t["ox_cat_prompt"], _kb(buttons, [_nav(t)]), edit)


@router.callback_query(ExCb.filter(F.a == "add"))
async def start_add(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(ox={})
    await _ask_cat(callback.message, t, state, edit=True)


async def _ask_desc(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    ox = (await state.get_data()).get("ox") or {}
    await state.set_state(ExSt.desc)
    await _show(target, t["ox_desc_prompt"].format(cat=escape(category_label(ox.get("cat", ""), _lang(t)), quote=False)),
                _kb([], [_nav(t)]), edit)


@router.callback_query(ExSt.cat, ExCb.filter(F.a == "cat"))
async def pick_cat(callback: CallbackQuery, callback_data: ExCb, t: Dict[str, str], state: FSMContext) -> None:
    if callback_data.i >= len(DEFAULT_CATEGORIES):
        await callback.answer()
        return
    await callback.answer()
    await state.update_data(ox={"cat": DEFAULT_CATEGORIES[callback_data.i][0]})
    await _ask_desc(callback.message, t, state, edit=True)


@router.message(ExSt.cat, F.text)
async def type_cat(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    cat = clean_text(message.text, 40)
    if cat:
        await state.update_data(ox={"cat": cat})
        await _ask_desc(message, t, state, edit=False)


async def _ask_amount(target: Message, t: Dict[str, str], state: FSMContext, edit: bool) -> None:
    await state.set_state(ExSt.amount)
    await _show(target, t["ox_amount_prompt"], _kb([], [_nav(t)]), edit)


@router.message(ExSt.desc, F.text)
async def type_desc(message: Message, t: Dict[str, str], state: FSMContext) -> None:
    desc = clean_text(message.text, 200)
    if len(desc) < 2:
        await message.answer(t["ox_desc_short"], reply_markup=_kb([], [_nav(t)]))
        return
    ox = dict((await state.get_data()).get("ox") or {})
    ox["desc"] = desc
    await state.update_data(ox=ox)
    await _ask_amount(message, t, state, edit=False)


@router.message(ExSt.amount, F.text)
async def type_amount(message: Message, t: Dict[str, str], user: User, state: FSMContext,
                      warehouse: WarehouseService) -> None:
    value = parse_number(message.text)
    if value is None or value <= 0 or value > 10**12:
        key = "ox_amount_nan" if value is None else "ox_amount_bad"
        await message.answer(t[key].format(raw=escape(message.text[:30])), reply_markup=_kb([], [_nav(t)]))
        return
    ox = (await state.get_data()).get("ox") or {}
    if not ox.get("cat") or not ox.get("desc") or warehouse.other is None:
        await state.clear()
        await message.answer(t["wh_expired"])
        return
    try:
        await warehouse.other.add(warehouse.today(), ox["cat"], ox["desc"], value, user.full_name)
    except Exception:
        logger.exception("«Прочие расходы» ga yozib bo'lmadi")
        await message.answer(t["wh_save_error"])
        return
    await state.clear()
    await message.answer(
        t["ox_saved"].format(cat=escape(category_label(ox["cat"], _lang(t)), quote=False), desc=escape(ox["desc"]),
                             amount=fmt_money(int(round(value))), tab=escape(warehouse.other.tab)),
        reply_markup=_kb([_b(t["ox_btn_more"], "add")], [_nav(t, back=False)]))


@router.callback_query(StateFilter(ExSt), ExCb.filter(F.a == "back"))
async def step_back(callback: CallbackQuery, t: Dict[str, str], state: FSMContext,
                    warehouse: WarehouseService) -> None:
    await callback.answer()
    st = await state.get_state()
    if st == ExSt.amount.state:
        await _ask_desc(callback.message, t, state, edit=True)
    elif st == ExSt.desc.state:
        await _ask_cat(callback.message, t, state, edit=True)
    else:
        await _menu(callback.message, t, state, warehouse, edit=True)


@router.callback_query(ExCb.filter(F.a == "cancel"))
async def cancel(callback: CallbackQuery, t: Dict[str, str], state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _show(callback.message, t["wh_cancelled"], None, True)


@router.callback_query(ExCb.filter())
async def expired(callback: CallbackQuery, t: Dict[str, str]) -> None:
    await callback.answer(t["wh_expired"], show_alert=True)
