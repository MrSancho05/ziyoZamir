from __future__ import annotations

from typing import Dict, Optional

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from app.core.permissions import Permission, Role, has_permission

# (ruxsat, tugma matni kaliti) — menyuda shu tartibda, 2 ustunli to'r bo'lib chiqadi
_MENU = (
    (Permission.MANAGE_WAREHOUSE, "btn_inflow"),
    (Permission.MANAGE_WAREHOUSE, "btn_outflow"),
    (Permission.MANAGE_WAREHOUSE, "btn_products"),
    (Permission.VIEW_DEFICIT, "btn_deficit"),
    (Permission.MARK_ATTENDANCE, "btn_attendance"),
    (Permission.MARK_EXPENSES, "btn_shop"),
    (Permission.VIEW_OWN_ATTENDANCE, "btn_my"),
    (Permission.VIEW_ATTENDANCE, "btn_check"),
    (Permission.VIEW_EXPENSES, "btn_shop_check"),
    (Permission.VIEW_TOP_PRODUCTS, "btn_top_products"),
    (Permission.MANAGE_USERS, "btn_users"),
)


def main_menu_kb(t: Dict[str, str], role: Optional[Role]) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    for permission, key in _MENU:
        if has_permission(role, permission):
            kb.add(KeyboardButton(text=t[key]))
    kb.adjust(2)
    kb.row(KeyboardButton(text=t["btn_lang"]))     # pastki qator: to'liq kenglikda
    return kb.as_markup(resize_keyboard=True)
