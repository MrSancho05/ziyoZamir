import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import User as TgUser

from app.core.permissions import Role
from app.keyboards.inline import request_kb
from app.locales.texts import LANG_NAMES, get_texts
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)


def guess_lang(language_code: str | None) -> str:
    return "ru" if language_code and language_code.lower().startswith("ru") else "uz"


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Xabar yuboradi. Foydalanuvchi botni bloklagan bo'lsa ham dastur yiqilmaydi."""
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramAPIError as exc:
        logger.warning("Xabar yuborib bo'lmadi (chat_id=%s): %s", chat_id, exc)
        return False


async def register_access_request(bot: Bot, repo: UserRepository,
                                  tg_user: TgUser, lang: str) -> bool:
    """So'rovni bazaga yozadi va barcha adminlarga tugmalar bilan yuboradi."""
    created = await repo.create_pending(tg_user.id, tg_user.full_name, tg_user.username, lang)
    if not created:
        return False

    delivered = 0
    for admin in await repo.list_by_role(Role.ADMIN):
        t = get_texts(admin.lang)
        text = t["admin_request"].format(
            name=escape(tg_user.full_name),
            username=f"@{escape(tg_user.username)}" if tg_user.username else t["no_username"],
            uid=tg_user.id,
            lang=LANG_NAMES.get(lang, lang),
        )
        if await safe_send(bot, admin.id, text, reply_markup=request_kb(t, tg_user.id)):
            delivered += 1

    if delivered == 0:
        logger.error(
            "Yangi so'rov (id=%s) hech bir adminga yetib bormadi. "
            "Adminlar botga /start bosganmi va ADMIN_IDS to'g'rimi?", tg_user.id
        )
    return True


async def check_admins_reachable(bot: Bot, admin_ids: frozenset[int]) -> None:
    """Ishga tushganda: adminga xabar yubora olamizmi? Yubora olmasak ogohlantiramiz."""
    for uid in admin_ids:
        try:
            await bot.get_chat(uid)
        except TelegramAPIError as exc:
            logger.warning(
                "Admin %s ga xabar yubora olmaymiz (u botga /start bosmagan "
                "yoki ID noto'g'ri bo'lishi mumkin): %s", uid, exc
            )
