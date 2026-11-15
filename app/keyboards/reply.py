from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.core.permissions import Permission, Role, has_permission


def main_menu_kb(t: dict[str, str], role: Role | None) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if has_permission(role, Permission.VIEW_DEFICIT):
        rows.append([KeyboardButton(text=t["btn_deficit"])])
    if has_permission(role, Permission.MARK_ATTENDANCE):
        rows.append([KeyboardButton(text=t["btn_attendance"])])
    if has_permission(role, Permission.MARK_EXPENSES):
        rows.append([KeyboardButton(text=t["btn_shop"])])
    if has_permission(role, Permission.VIEW_OWN_ATTENDANCE):
        rows.append([KeyboardButton(text=t["btn_my"])])
    if has_permission(role, Permission.VIEW_ATTENDANCE):
        rows.append([KeyboardButton(text=t["btn_check"])])
    if has_permission(role, Permission.VIEW_EXPENSES):
        rows.append([KeyboardButton(text=t["btn_shop_check"])])
    if has_permission(role, Permission.VIEW_TOP_PRODUCTS):
        rows.append([KeyboardButton(text=t["btn_top_products"])])
    if has_permission(role, Permission.MANAGE_USERS):
        rows.append([KeyboardButton(text=t["btn_users"])])
    rows.append([KeyboardButton(text=t["btn_lang"])])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
