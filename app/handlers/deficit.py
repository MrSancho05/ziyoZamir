import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission, has_permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import MenuCb, PageCb
from app.keyboards.inline import deficit_menu_kb, pagination_kb
from app.locales.texts import all_texts
from app.models import DeficitItem, User
from app.services.deficit import load_deficit_items
from app.services.sheets import SheetsClient
from app.storage.memory import UserStore
from app.utils.formatting import fmt_num
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)
router = Router(name="deficit")

# Bu routerdagi hamma narsa "tanqis ro'yxatni ko'rish" ruxsatini talab qiladi
router.message.filter(HasPermission(Permission.VIEW_DEFICIT))
router.callback_query.filter(HasPermission(Permission.VIEW_DEFICIT))


def _menu_kb(t: dict[str, str], user: User):
    return deficit_menu_kb(t, can_download=has_permission(user.role, Permission.DOWNLOAD_REPORTS))


def _build_page_text(items: list[DeficitItem], page: int, total_pages: int,
                     total: int, t: dict[str, str]) -> str:
    header = (
        f"🚨 <b>{t['total_count']}: {total} {t['unit']}</b>\n"
        f"📄 {t['page']}: {page + 1}/{total_pages}\n"
        f"----------------------------------------\n\n"
    )
    body = "\n\n".join(
        f"⚠️ <b>{escape(i.name)}</b>\n"
        f"   {t['norm']}: {fmt_num(i.norm)} {t['unit']} | "
        f"{t['stock']}: {fmt_num(i.stock)} {t['unit']}"
        for i in items
    )
    return header + body


@router.message(F.text.in_(all_texts("btn_deficit")))
async def show_deficit(message: Message, t: dict[str, str], user: User,
                       store: UserStore, sheets: SheetsClient) -> None:
    try:
        items = await load_deficit_items(sheets)
    except Exception:
        logger.exception("Google Sheets'dan o'qishda xatolik")
        await message.answer(t["error"])
        return

    if not items:
        await message.answer(t["no_deficit"])
        return

    store.set_deficit(user.id, items)
    await message.answer(
        t["count_title"].format(count=len(items)),
        reply_markup=_menu_kb(t, user),
    )


@router.callback_query(PageCb.filter())
async def show_page(callback: CallbackQuery, callback_data: PageCb, t: dict[str, str],
                    user: User, store: UserStore, settings: Settings) -> None:
    items = store.get_deficit(user.id)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    chunk, page, total_pages = paginate(items, callback_data.page, settings.page_size)
    await callback.message.edit_text(
        _build_page_text(list(chunk), page, total_pages, len(items), t),
        reply_markup=pagination_kb(page, total_pages, t),
    )
    await callback.answer()


@router.callback_query(MenuCb.filter(F.action == "back"))
async def back_to_menu(callback: CallbackQuery, t: dict[str, str],
                       user: User, store: UserStore) -> None:
    items = store.get_deficit(user.id)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    await callback.message.edit_text(
        t["count_title"].format(count=len(items)),
        reply_markup=_menu_kb(t, user),
    )
    await callback.answer()


@router.callback_query(MenuCb.filter(F.action == "noop"))
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
