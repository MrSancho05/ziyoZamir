"""Sklad klaviaturalari: hammasi 2 ustunli to'r (adjust(2)), pastki navigatsiya alohida to'liq qatorda."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.keyboards.callbacks import WhCb
from app.keyboards.webapp import dashboard_rows

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


def cancel_row(t: Dict[str, str], back: bool = True) -> List[InlineKeyboardButton]:
    """[⬅️ Orqaga] [❌ Bekor qilish]. Orqaga — bitta qadam ortga (holat saqlanadi), bekor — jarayonni yopadi."""
    row = [_b(t["wh_btn_step_back"], "back")] if back else []
    return row + [_b(t["wh_btn_cancel"], "cancel")]


def _cat_label(label: str) -> str:
    """Standart kategoriyalarda emoji bor; foydalanuvchi qo'shganlariga 📂 qo'yiladi."""
    label = str(label)
    return _short(label, 26) if label and not label[0].isalnum() else f"📂 {_short(label, 24)}"


def categories_kb(t: Dict[str, str], cats: Sequence[str], cart_size: int) -> InlineKeyboardMarkup:
    """`cats`: foydalanuvchi tilidagi nomlar (tartibi holatdagi kanonik ro'yxat bilan bir xil)."""
    buttons = [_b(_cat_label(c), "cat", i) for i, c in enumerate(cats)]
    nav = []
    if cart_size:
        nav.append([_b(f"🛒 ({cart_size})", "cart")])
    nav.append(cancel_row(t, back=False))          # birinchi qadam: orqaga yo'q
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
    nav.append(cancel_row(t, back=False))          # "📂 Kategoriyalar" — orqaga tugmasi vazifasini bajaradi
    return grid(buttons, nav)


def search_kb(t: Dict[str, str], names: Sequence[str], allow_new: bool) -> InlineKeyboardMarkup:
    buttons = [_b(_short(n), "p", i) for i, n in enumerate(names)]
    nav: List[List[InlineKeyboardButton]] = []
    if allow_new:
        nav.append([_b(t["wh_btn_new_product"], "new")])
    nav.append([_b(t["wh_btn_cats"], "cats")])
    nav.append(cancel_row(t, back=False))
    return grid(buttons, nav)


def mode_kb(t: Dict[str, str], pack: str, unit: str) -> InlineKeyboardMarkup:
    return grid([_b(t["wh_btn_pack"].format(pack=pack, unit=unit), "mode", k="pack"),
                 _b(t["wh_btn_unit"].format(unit=unit), "mode", k="unit")], [cancel_row(t)])


def price_kb(t: Dict[str, str], last_price: Optional[str], allow_later: bool = True) -> InlineKeyboardMarkup:
    buttons = [_b(t["wh_btn_last_price"].format(price=last_price), "lp")] if last_price else []
    if allow_later:
        buttons.append(_b(t["wh_btn_price_later"], "pl"))
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
    nav.append(cancel_row(t, back=has_items))     # orqaga: oxirgi qo'shilgan tovarni qayta tahrirlash
    return grid(buttons, nav)


def remove_kb(t: Dict[str, str], labels: Sequence[str]) -> InlineKeyboardMarkup:
    buttons = [_b(f"🗑 {i + 1}. {_short(n, 22)}", "dl", i) for i, n in enumerate(labels)]
    return grid(buttons, [[_b(t["wh_btn_back"], "cart")]])


# ---------- Mahsulotlar katalogi ----------
def pm_menu_kb(t: Dict[str, str]) -> InlineKeyboardMarkup:
    return grid([_b(t["wh_pm_add"], "pm", k="add"), _b(t["wh_pm_rename"], "pm", k="ren"),
                 _b(t["wh_pm_delete"], "pm", k="del"), _b(t["wh_pm_cats"], "cm"),
                 _b(t["wh_pm_unpriced"], "unp"), _b(t["wh_pm_masters"], "dm", k="master"),
                 _b(t["wh_pm_units"], "dm", k="unit")],
                [cancel_row(t, back=False)])


def np_category_kb(t: Dict[str, str], cats: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(_cat_label(c), "ncat", i) for i, c in enumerate(cats)], [cancel_row(t)])


def suggest_kb(t: Dict[str, str]) -> InlineKeyboardMarkup:
    return grid([_b(t["wh_btn_yes_short"], "sg", k="yes"), _b(t["wh_btn_other_cat"], "sg", k="no")],
                [cancel_row(t)])


# ---------- 🏷 Kategoriyalar ----------
def cat_menu_kb(t: Dict[str, str]) -> InlineKeyboardMarkup:
    return grid([_b(t["wh_cat_add"], "cmk", k="add"), _b(t["wh_cat_rename"], "cmk", k="ren"),
                 _b(t["wh_cat_delete"], "cmk", k="del")], [cancel_row(t, back=False)])


def cat_pick_kb(t: Dict[str, str], labels: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(_cat_label(c), "cpk", i) for i, c in enumerate(labels)],
                [[_b(t["wh_btn_back"], "cm")], cancel_row(t, back=False)])


# ---------- 👷 Ustalar / 📏 O'lchov birliklari (Справочник) ----------
def dir_menu_kb(t: Dict[str, str], kind: str, has_items: bool) -> InlineKeyboardMarkup:
    buttons = [_b(t["wh_dir_add"], "dma", k=kind)]
    if has_items:
        buttons.append(_b(t["wh_dir_del"], "dmd", k=kind))
    return grid(buttons, [[_b(t["wh_btn_back"], "pmm")], cancel_row(t, back=False)])


def dir_pick_kb(t: Dict[str, str], kind: str, values: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(f"🗑 {_short(v, 24)}", "dmx", i, k=kind) for i, v in enumerate(values)],
                [[_b(t["wh_btn_back"], "dm", k=kind)], cancel_row(t, back=False)])


# ---------- 💰 Narxi yo'q tovarlar ----------
def unpriced_kb(t: Dict[str, str], labels: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(f"💰 {_short(n, 26)}", "up", i) for i, n in enumerate(labels)], [cancel_row(t, back=False)])


def unpriced_price_kb(t: Dict[str, str], last_price: Optional[str]) -> InlineKeyboardMarkup:
    buttons = [_b(t["wh_btn_last_price"].format(price=last_price), "ulp")] if last_price else []
    return grid(buttons, [[_b(t["wh_btn_back"], "unp")], cancel_row(t, back=False)], cols=1)


def flows_reminder_kb(t: Dict[str, str], unpriced: int) -> InlineKeyboardMarkup:
    """17:30 eslatmasidagi tezkor tugmalar."""
    buttons = [_b(t["flows_btn_in"], "go", k="in"), _b(t["flows_btn_out"], "go", k="out")]
    nav = [[_b(t["wh_btn_unpriced"].format(count=unpriced), "unp")]] if unpriced else []
    return grid(buttons, nav + dashboard_rows(t))


def np_unit_kb(t: Dict[str, str], units: Sequence[str]) -> InlineKeyboardMarkup:
    return grid([_b(u, "unit", i) for i, u in enumerate(units)], [cancel_row(t)])


def quick_value_kb(t: Dict[str, str], action: str, label: str) -> InlineKeyboardMarkup:
    return grid([_b(label, action)], [cancel_row(t)])


def confirm_kb(t: Dict[str, str], yes_label: str) -> InlineKeyboardMarkup:
    return grid([_b(yes_label, "ok")], [cancel_row(t)], cols=1)
