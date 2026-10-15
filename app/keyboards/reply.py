from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.core.permissions import Permission, Role, has_permission


def main_menu_kb(t: dict[str, str], role: Role | None) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if has_permission(role, Permission.VIEW_DEFICIT):
        rows.append([KeyboardButton(text=t["btn_deficit"])])
    if has_permission(role, Permission.MANAGE_USERS):
        rows.append([KeyboardButton(text=t["btn_users"])])
    rows.append([KeyboardButton(text=t["btn_lang"])])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
