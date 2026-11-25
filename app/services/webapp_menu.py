"""Telegram chap pastki "Menu" tugmasi: FAQAT Boss va Admin uchun 📊 Dashboard (Mini App); Sklad nazoratchisi va
Ishchiga oddiy menyu (MenuButtonDefault — avval o'rnatilgan tugma ham olib tashlanadi).

Tugma har bir chat uchun alohida o'rnatiladi (chat_id bilan): umumiy (hamma uchun) tugma qo'yilsa, Ishchi ham
ko'rib, ochganda "ruxsat yo'q" xabarini olardi. Rol yoki til o'zgarganda qayta moslanadi."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

from app.core.permissions import Role
from app.keyboards.webapp import dashboard_url, webapp_enabled
from app.models import User
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)

MENU_TEXT = "📊 Dashboard"
DASHBOARD_ROLES = (Role.BOSS, Role.ADMIN)        # sklad nazoratchisi — oddiy menyu (MenuButtonDefault)


async def sync_menu_button(bot: Bot, user: User) -> None:
    """Bitta foydalanuvchining Menu tugmasini uning roliga moslaydi. Xato bo'lsa jim o'tadi."""
    if not webapp_enabled():
        return
    try:
        if user.is_active and user.role in DASHBOARD_ROLES:
            await bot.set_chat_menu_button(
                chat_id=user.id,
                menu_button=MenuButtonWebApp(text=MENU_TEXT, web_app=WebAppInfo(url=dashboard_url(user.lang))))
        else:
            await bot.set_chat_menu_button(chat_id=user.id, menu_button=MenuButtonDefault())
    except Exception as exc:                 # botni bloklagan / hali yozmagan foydalanuvchi
        logger.debug("Menu tugmasini %s uchun o'rnatib bo'lmadi: %s", user.id, exc)


async def sync_all_menu_buttons(bot: Bot, repo: UserRepository) -> int:
    """Ishga tushganda: barcha faol foydalanuvchilar. Boss/Admin soni qaytariladi."""
    if not webapp_enabled():
        return 0
    users = await repo.list_active()
    for user in users:
        await sync_menu_button(bot, user)
        await asyncio.sleep(0.05)            # Telegram limitlariga tegmaslik uchun
    count = sum(1 for u in users if u.role in DASHBOARD_ROLES)
    logger.info("📊 Dashboard Menu tugmasi o'rnatildi: %d ta Boss/Admin", count)
    return count
