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
    (Permission.MANAGE_WAREHOUSE, "btn_other_exp"),   # 🧾 Boshqa xarajatlar
    (Permission.VIEW_DEFICIT, "btn_deficit"),
    (Permission.MARK_ATTENDANCE, "btn_attendance"),
    (Permission.MARK_EXPENSES, "btn_shop"),
    (Permission.VIEW_OWN_ATTENDANCE, "btn_my"),
    (Permission.VIEW_ATTENDANCE, "btn_check"),
    (Permission.VIEW_EXPENSES, "btn_shop_check"),
    (Permission.VIEW_TOP_PRODUCTS, "btn_top_products"),
    (Permission.MANAGE_USERS, "btn_users"),
)


def main_menu_kb(t: Dict[str, str], role: Optional[Role], real_role: Optional[Role] = None) -> ReplyKeyboardMarkup:
    """`real_role` = ADMIN va `role` boshqa bo'lsa: admin 🎭 rolni sinayapti — "🔙 Adminga qaytish" chiqadi.

    📊 Jonli Dashboard bu yerda YO'Q: u faqat chap pastki Menu tugmasida (services/webapp_menu.py) va
    Boss/Admin hisobotlari ostidagi inline tugmada (keyboards/webapp.py)."""
    kb = ReplyKeyboardBuilder()
    for permission, key in _MENU:
        if has_permission(role, permission):
            kb.add(KeyboardButton(text=t[key]))
    if role == Role.ADMIN:
        kb.add(KeyboardButton(text=t["btn_role_test"]))
    kb.add(KeyboardButton(text=t["btn_help"]))
    kb.adjust(2)
    if real_role == Role.ADMIN and role != Role.ADMIN:
        kb.row(KeyboardButton(text=t["btn_admin_back"]))    # 1 bosishda admin rejimiga qaytish
    kb.row(KeyboardButton(text=t["btn_lang"]))     # pastki qator: to'liq kenglikda
    return kb.as_markup(resize_keyboard=True)
