from html import escape

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.core.permissions import Permission, has_permission
from app.keyboards.inline import review_kb
from app.keyboards.reply import main_menu_kb
from app.models import User
from app.storage.users import UserRepository

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, t: dict[str, str], user: User, repo: UserRepository) -> None:
    await message.answer(
        t["welcome"].format(name=escape(user.full_name), role=t[f"role_{user.role.value}"]),
        reply_markup=main_menu_kb(t, user.role),
    )

    # Admin botga kirganda kutayotgan so'rovlar haqida eslatiladi
    if has_permission(user.role, Permission.MANAGE_USERS):
        pending = await repo.list_pending()
        if pending:
            await message.answer(
                t["pending_notice"].format(count=len(pending)),
                reply_markup=review_kb(t),
            )
