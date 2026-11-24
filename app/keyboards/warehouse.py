"""Sklad klaviaturalari: hammasi 2 ustunli to'r (adjust(2)), pastki navigatsiya alohida to'liq qatorda."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.keyboards.callbacks import WhCb

MAX_LABEL = 30


def _short(text: str, limit: int = MAX_LABEL) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _b(text: str, a: str, i: int = 0, k: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=WhCb(a=a, i=i, k=k).pack())


def grid(buttons: Sequence[InlineKeyboardButton],
         nav: Sequence[Sequence[InlineKeyboardButton]] = (), cols: int = 2) -> InlineKeyboardMarkup:
    """Asosiy tugmalar `cols` ustunli to'r; `nav` qatorlari (Orqaga / Bekor qilish) pastda alohida."""
    kb = InlineKeyboardBuilder()
    for btn in buttons:
        kb.add(btn)
    kb.adjust(cols)
    for row in nav:
        if row:
            kb.row(*row)
    return kb.as_markup()


def cancel_row(t: Dict[str, str]) -> List[InlineKeyboardButton]:
    return [_b(t["wh_btn_cancel"], "cancel")]


def categories_kb(t: Dict[str, str], cats: Sequence[str], cart_size: int) -> InlineKeyboardMarkup:
    buttons = [_b(f"📂 {_short(c, 24)}", "cat", i) for i, c in enumerate(cats)]
    nav = []
    if cart_size:
        nav.append([_b(f"🛒 ({cart_size})", "cart")])
    nav.append(cancel_row(t))
    return grid(buttons, nav)


def products_kb(t: Dict[str, str], names: Sequence[str], page: int, pages: int,
                offset: int = 0) -> InlineKeyboardMarkup:
    buttons = [_b(_short(n), "p", offset + i) for i, n in enumerate(names)]
    nav: List[List[InlineKeyboardButton]] = []
    if pages > 1:
        nav.append([
            _b("◀️", "pg", page - 1) if page > 0 else _b("·", "noop"),
            _b(f"{page + 1}/{pages}", "noop"),
            _b("▶️", "pg", page + 1) if page < pages - 1 else _b("·", "noop"),
        ])
    nav.append([_b(t["wh_btn_cats"], "cats")])
    nav.append(cancel_row(t))
    return grid(buttons, nav)


def search_kb(t: Dict[str, str], names: Sequence[str], allow_new: bool) -> InlineKeyboardMarkup:
    buttons = [_b(_short(n), "p", i) for i, n in enumerate(names)]
    nav: List[List[InlineKeyboardButton]] = []
    if allow_new:
        nav.append([_b(t["wh_btn_new_product"], "new")])
    nav.append([_b(t["wh_btn_cats"], "cats")])
    nav.append(cancel_row(t))
    return grid(buttons, nav)


def mode_kb(t: Dict[str, str], pack: str, unit: str) -> InlineKeyboardMarkup:
    return grid([_b(t["wh_btn_pack"].format(pack=pack, unit=unit), "mode", k="pack"),
                 _b(t["wh_btn_unit"].format(unit=unit), "mode", k="unit")], [cancel_row(t)])


def price_kb(t: Dict[str, str], last_price: Optional[str]) -> InlineKeyboardMarkup:
    buttons = [_b(t["wh_btn_last_price"].format(price=last_price), "lp")] if last_price else []
    return grid(buttons, [cancel_row(t)], cols=1)


def cancel_kb(t: Dict[str, str]) -> InlineKeyboardMarkup:
    return grid([], [cancel_row(t)])


def who_kb(t: Dict[str, str], names: Sequence[str], new_label: str) -> InlineKeyboardMarkup:
    buttons = [_b(_short(n, 24), "who", i) for i, n in enumerate(names)]
    return grid(buttons, [[_b(new_label, "whonew")], cancel_row(t)])


def purpose_kb(t: Dict[str, str], recent: Sequence[str]) -> InlineKeyboardMarkup:
    buttons = [_b(_short(n, 24), "pur", i) for i, n in enumerate(recent)]
    return grid(buttons, [[_b(t["wh_btn_skip"], "skip")], cancel_row(t)])


def cart_kb(t: Dict[str, str], has_items: bool) -> InlineKeyboardMarkup:
    buttons = [_b(t["wh_btn_more"], "more")]
    if has_items:
        buttons.append(_b(t["wh_btn_remove"], "rm"))
    nav = [[_b(t["wh_btn_save"], "save")]] if has_items else []
    nav.append(cancel_row(t))
    return grid(buttons, nav)


def remove_kb(t: Dict[str, str], labels: Sequence[str]) -> InlineKeyboardMarkup:
    buttons = [_b(f"🗑 {i + 1}. {_short(n, 22)}", "dl", i) for i, n in enumerate(labels)]
    return grid(buttons, [[_b(t["wh_btn_back"], "cart")]])


# ---------- Mahsulotlar katalogi ----------
def pm_menu_kb(t: Dict[str, str]) -> InlineKeyboardMarkup:
    return grid([_b(t["wh_pm_add"], "pm", k="add"), _b(t["wh_pm_rename"], "pm", k="ren"),
                 _b(t["wh_pm_delete"], "pm", k="del")], [cancel_row(t)])


def np_category_kb(t: Dict[str, str], cats: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(_short(c, 24), "ncat", i) for i, c in enumerate(cats)], [cancel_row(t)])


def np_unit_kb(t: Dict[str, str], units: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(u, "unit", i) for i, u in enumerate(units)], [cancel_row(t)])


def quick_value_kb(t: Dict[str, str], action: str, label: str) -> InlineKeyboardMarkup:
    return grid([_b(label, action)], [cancel_row(t)])


def confirm_kb(t: Dict[str, str], yes_label: str) -> InlineKeyboardMarkup:
    return grid([_b(yes_label, "ok"), _b(t["wh_btn_cancel"], "cancel")])
