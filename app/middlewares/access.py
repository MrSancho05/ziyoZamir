from dataclasses import replace
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.keyboards.callbacks import LangCb
from app.keyboards.inline import language_kb
from app.locales.texts import get_texts
from app.services.access import guess_lang
from app.storage.users import UserRepository


class AccessMiddleware(BaseMiddleware):
    """Faqat faol foydalanuvchilarni o'tkazadi.

    Faol foydalanuvchi: handlerlarga `user`, `lang`, `t` uzatiladi.
    Ruxsatsiz foydalanuvchi: faqat til tanlash tugmalariga ruxsat beriladi.
    Til tanlangach so'rov yaratilib, adminlarga yuboriladi (handlers/language.py).
    """

    def __init__(self, repo: UserRepository) -> None:
        self._repo = repo

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return None

        user = await self._repo.get(tg_user.id)

        if user is not None and user.is_active:
            if user.full_name != tg_user.full_name or user.username != tg_user.username:
                await self._repo.update_profile(user.id, tg_user.full_name, tg_user.username)
                user = replace(user, full_name=tg_user.full_name, username=tg_user.username)
            data["user"] = user
            data["lang"] = user.lang
            data["t"] = get_texts(user.lang)
            return await handler(event, data)

        # ---- Ruxsati yo'q foydalanuvchi ----
        lang = user.lang if user else guess_lang(tg_user.language_code)
        t = get_texts(lang)
        data["lang"] = lang
        data["t"] = t

        if isinstance(event, CallbackQuery):
            if event.data and event.data.startswith(f"{LangCb.__prefix__}:"):
                return await handler(event, data)  # faqat til tanlash o'tadi
            await event.answer(
                t["no_access_pending"] if user else t["choose_language"], show_alert=True
            )
            return None

        if isinstance(event, Message):
            if user is None:
                # Birinchi uchrashuv: avval til tanlanadi
                await event.answer(t["choose_language"], reply_markup=language_kb())
            else:
                await event.answer(t["no_access_pending"], reply_markup=language_kb())
        return None
