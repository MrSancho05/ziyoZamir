"""🎭 Rolni sinash (faqat haqiqiy admin) va /help (faol rolga ruxsat etilgan amallar).

Rolni sinash bazadagi admin huquqini O'ZGARTIRMAYDI: tanlangan rol faqat shu sessiya xotirasida
(UserStore) saqlanadi va AccessMiddleware har bir so'rovda `user.role` ni vaqtincha almashtiradi.
"🔙 Adminga qaytish" yoki /admin — bir bosishda to'liq admin rejimi."""
from __future__ import annotations

from typing import Dict, Optional

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.permissions import Role
from app.keyboards.callbacks import RoleCb
from app.keyboards.inline import role_pick_kb
from app.keyboards.reply import main_menu_kb
from app.locales.texts import all_texts
from app.models import User
from app.storage.memory import UserStore

router = Router(name="roles")


class RealAdmin(BaseFilter):
    """Haqiqiy (bazadagi) roli admin bo'lgan foydalanuvchi — hozir qaysi rolni sinayotganidan qat'i nazar."""

    async def __call__(self, event, user: Optional[User] = None) -> bool:
        return user is not None and (user.real_role or user.role) == Role.ADMIN


@router.message(F.text.in_(all_texts("btn_role_test")), RealAdmin())
async def open_role_test(message: Message, t: Dict[str, str]) -> None:
    await message.answer(t["role_pick"], reply_markup=role_pick_kb(t))


@router.callback_query(RoleCb.filter(), RealAdmin())
async def pick_role(callback: CallbackQuery, callback_data: RoleCb, t: Dict[str, str], user: User,
                    store: UserStore, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback_data.r == Role.ADMIN.value:
        store.clear_role_override(user.id)
        await callback.message.answer(t["role_restored"], reply_markup=main_menu_kb(t, Role.ADMIN))
        return
    role = Role(callback_data.r)
    store.set_role_override(user.id, role)
    store.clear_awaiting(user.id)
    await callback.message.answer(t["role_switched"].format(role=t[f"role_{role.value}"]),
                                  reply_markup=main_menu_kb(t, role, real_role=Role.ADMIN))


@router.message(Command("admin"), RealAdmin())
@router.message(F.text.in_(all_texts("btn_admin_back")), RealAdmin())
async def back_to_admin(message: Message, t: Dict[str, str], user: User, store: UserStore,
                        state: FSMContext) -> None:
    await state.clear()
    store.clear_role_override(user.id)
    store.clear_awaiting(user.id)
    await message.answer(t["role_restored"], reply_markup=main_menu_kb(t, Role.ADMIN))


def role_help_text(t: Dict[str, str], role: Optional[Role]) -> str:
    """Faqat faol rolga ruxsat etilgan amallar (yuqori rollarning yashirin imkoniyatlari aytilmaydi)."""
    role = role or Role.WORKER
    text = t[f"caps_{role.value}"]
    if role == Role.ADMIN:                          # 🤖 AI yordamchi faqat adminda
        text += "\n\n" + t["caps_ai_tail"]
    return text


# Eski ❓ Yordam tugmasi telefonda qolib ketgan bo'lsa ham ishlaydi (menyu yangilanadi)
@router.message(Command("help"))
@router.message(F.text.in_(all_texts("btn_help")))
async def show_help(message: Message, t: Dict[str, str], user: User) -> None:
    text = role_help_text(t, user.role)
    if user.real_role == Role.ADMIN and user.role != Role.ADMIN:
        text += "\n\n" + t["help_role_test_note"].format(role=t[f"role_{user.role.value}"])
    await message.answer(text, reply_markup=main_menu_kb(t, user.role, real_role=user.real_role))
