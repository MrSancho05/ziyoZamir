from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from app.keyboards.callbacks import LangCb
from app.keyboards.inline import language_kb
from app.keyboards.reply import main_menu_kb
from app.locales.texts import TEXTS, all_texts, get_texts
from app.models import User
from app.services.access import register_access_request
from app.storage.users import UserRepository

router = Router(name="language")


@router.message(F.text.in_(all_texts("btn_lang")))
async def ask_language(message: Message, t: dict[str, str]) -> None:
    await message.answer(t["choose_language"], reply_markup=language_kb())


@router.callback_query(LangCb.filter())
async def set_language(callback: CallbackQuery, callback_data: LangCb, repo: UserRepository,
                       bot: Bot, user: User | None = None) -> None:
    code = callback_data.code
    if code not in TEXTS:
        await callback.answer()
        return
    t = get_texts(code)

    # 1) Faol foydalanuvchi tilni o'zgartiryapti
    if user is not None:
        await repo.set_lang(user.id, code)
        await callback.message.answer(t["lang_changed"], reply_markup=main_menu_kb(t, user.role))
        await callback.answer()
        return

    # 2) Ruxsatsiz foydalanuvchi: til tanlandi
    tg_user = callback.from_user
    existing = await repo.get(tg_user.id)
    if existing is None:
        # Birinchi marta: so'rov yaratiladi va adminlarga yuboriladi
        await register_access_request(bot, repo, tg_user, code)
        text = t["no_access_new"]
    else:
        # So'rov allaqachon yuborilgan, faqat til almashtiriladi
        if existing.lang == code:
            await callback.answer()
            return
        await repo.set_lang(tg_user.id, code)
        text = t["no_access_pending"]

    await callback.message.edit_text(text, reply_markup=language_kb())
    await callback.answer()
