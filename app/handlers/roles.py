"""🎭 Rolni sinash (faqat haqiqiy admin) va ❓ Yordam (har bir rol uchun alohida qisqa yo'riqnoma).

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


@router.message(F.text.in_(all_texts("btn_help")))
async def show_help(message: Message, t: Dict[str, str], user: User) -> None:
    role = user.role.value if user.role else Role.WORKER.value
    text = t[f"help_{role}"]
    if user.real_role == Role.ADMIN and user.role != Role.ADMIN:
        text += "\n\n" + t["help_role_test_note"].format(role=t[f"role_{role}"])
    await message.answer(text, reply_markup=main_menu_kb(t, user.role, real_role=user.real_role))
