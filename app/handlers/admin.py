from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.core.permissions import Permission, Role
from app.filters.permission import HasPermission
from app.keyboards.callbacks import AccessCb
from app.keyboards.inline import (
    confirm_delete_kb,
    request_kb,
    user_card_kb,
    users_list_kb,
)
from app.keyboards.reply import main_menu_kb
from app.locales.texts import LANG_NAMES, all_texts, get_texts
from app.models import STATUS_PENDING, User
from app.services.access import safe_send
from app.storage.users import UserRepository

router = Router(name="admin")

# Bu routerdagi hamma narsa faqat adminlar uchun
router.message.filter(HasPermission(Permission.MANAGE_USERS))
router.callback_query.filter(HasPermission(Permission.MANAGE_USERS))


def _uname(t: dict[str, str], u: User) -> str:
    return f"@{escape(u.username)}" if u.username else t["no_username"]


def _card_text(t: dict[str, str], u: User) -> str:
    return t["user_card"].format(
        name=escape(u.full_name), username=_uname(t, u), uid=u.id,
        role=t[f"role_{u.role.value}"],
    )


def _request_text(t: dict[str, str], u: User) -> str:
    return t["admin_request"].format(
        name=escape(u.full_name), username=_uname(t, u), uid=u.id,
        lang=LANG_NAMES.get(u.lang, u.lang),
    )


async def _list_view(t: dict[str, str], repo: UserRepository):
    pending = await repo.list_pending()
    active = await repo.list_active()
    return t["users_title"], users_list_kb(t, pending, active)


@router.message(F.text.in_(all_texts("btn_users")))
async def users_menu(message: Message, t: dict[str, str], repo: UserRepository) -> None:
    text, kb = await _list_view(t, repo)
    await message.answer(text, reply_markup=kb)


@router.callback_query(AccessCb.filter(F.action == "list"))
async def back_to_list(callback: CallbackQuery, t: dict[str, str], repo: UserRepository) -> None:
    text, kb = await _list_view(t, repo)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(AccessCb.filter(F.action == "open"))
async def open_user(callback: CallbackQuery, callback_data: AccessCb, t: dict[str, str],
                    user: User, repo: UserRepository) -> None:
    target = await repo.get(callback_data.uid)
    if target is None:
        await callback.answer(t["already_processed"], show_alert=True)
        return

    if target.status == STATUS_PENDING:
        await callback.message.edit_text(
            _request_text(t, target), reply_markup=request_kb(t, target.id, with_back=True)
        )
    else:
        await callback.message.edit_text(
            _card_text(t, target),
            reply_markup=user_card_kb(t, target, is_self=target.id == user.id),
        )
    await callback.answer()


@router.callback_query(AccessCb.filter(F.action == "approve"))
async def approve(callback: CallbackQuery, callback_data: AccessCb, t: dict[str, str],
                  repo: UserRepository, bot: Bot) -> None:
    target = await repo.get(callback_data.uid)
    if target is None or target.status != STATUS_PENDING:
        await callback.answer(t["already_processed"], show_alert=True)
        return
    try:
        role = Role(callback_data.role)
    except ValueError:
        await callback.answer()
        return

    await repo.activate(target.id, role)
    await callback.message.edit_text(
        t["approved_text"].format(name=escape(target.full_name), role=t[f"role_{role.value}"])
    )
    await callback.answer()

    ut = get_texts(target.lang)
    await safe_send(
        bot, target.id,
        ut["role_assigned_user"].format(role=ut[f"role_{role.value}"]),
        reply_markup=main_menu_kb(ut, role),
    )


@router.callback_query(AccessCb.filter(F.action == "reject"))
async def reject(callback: CallbackQuery, callback_data: AccessCb, t: dict[str, str],
                 repo: UserRepository) -> None:
    target = await repo.get(callback_data.uid)
    if target is None or target.status != STATUS_PENDING:
        await callback.answer(t["already_processed"], show_alert=True)
        return

    await repo.delete(target.id)
    await callback.message.edit_text(t["rejected_text"].format(name=escape(target.full_name)))
    await callback.answer()


@router.callback_query(AccessCb.filter(F.action == "set"))
async def change_role(callback: CallbackQuery, callback_data: AccessCb, t: dict[str, str],
                      user: User, repo: UserRepository, bot: Bot) -> None:
    target = await repo.get(callback_data.uid)
    if target is None or target.role is None:
        await callback.answer(t["already_processed"], show_alert=True)
        return
    if target.id == user.id:
        await callback.answer(t["cannot_self"], show_alert=True)
        return
    try:
        role = Role(callback_data.role)
    except ValueError:
        await callback.answer()
        return
    if role == target.role:
        await callback.answer()
        return

    await repo.set_role(target.id, role)
    updated = await repo.get(target.id)
    await callback.message.edit_text(
        _card_text(t, updated), reply_markup=user_card_kb(t, updated, is_self=False)
    )
    await callback.answer()

    ut = get_texts(target.lang)
    await safe_send(
        bot, target.id,
        ut["role_changed_user"].format(role=ut[f"role_{role.value}"]),
        reply_markup=main_menu_kb(ut, role),
    )


@router.callback_query(AccessCb.filter(F.action == "del"))
async def ask_delete(callback: CallbackQuery, callback_data: AccessCb, t: dict[str, str],
                     user: User, repo: UserRepository) -> None:
    target = await repo.get(callback_data.uid)
    if target is None:
        await callback.answer(t["already_processed"], show_alert=True)
        return
    if target.id == user.id:
        await callback.answer(t["cannot_self"], show_alert=True)
        return

    await callback.message.edit_text(
        t["confirm_delete"].format(name=escape(target.full_name)),
        reply_markup=confirm_delete_kb(t, target.id),
    )
    await callback.answer()


@router.callback_query(AccessCb.filter(F.action == "delok"))
async def delete_user(callback: CallbackQuery, callback_data: AccessCb, t: dict[str, str],
                      user: User, repo: UserRepository, bot: Bot) -> None:
    target = await repo.get(callback_data.uid)
    if target is None:
        await callback.answer(t["already_processed"], show_alert=True)
        return
    if target.id == user.id:
        await callback.answer(t["cannot_self"], show_alert=True)
        return

    await repo.delete(target.id)
    await callback.message.edit_text(t["deleted_text"].format(name=escape(target.full_name)))
    await callback.answer()

    ut = get_texts(target.lang)
    await safe_send(bot, target.id, ut["access_revoked_user"], reply_markup=ReplyKeyboardRemove())
