import logging

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from app.keyboards.reply import main_menu_kb
from app.models import User

logger = logging.getLogger(__name__)
router = Router(name="fallback")


@router.callback_query()
async def denied(callback: CallbackQuery, t: dict[str, str]) -> None:
    """Hech bir handler olmagan (odatda ruxsat yetmagan) tugma bosilsa."""
    await callback.answer(t["no_permission"], show_alert=True)


@router.message()
async def unknown_message(message: Message, t: dict[str, str], user: User | None = None) -> None:
    """Hech bir handler mos kelmasa (masalan, rol o'zgargan, lekin Telegramdagi menyu eski).

    Jim turib qolmaymiz: tushuntirib, menyuni hozirgi rolga moslab yangilaymiz."""
    if user is None:
        return
    logger.info("Handler topilmadi: user_id=%s role=%s text=%r",
                user.id, user.role.value if user.role else None, message.text)
    await message.answer(t["unknown_command"], reply_markup=main_menu_kb(t, user.role))
