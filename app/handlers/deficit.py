import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission, has_permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import MenuCb, PageCb, ProductCb
from app.keyboards.inline import deficit_menu_kb, inspect_menu_kb, pagination_kb
from app.locales.texts import all_texts
from app.models import DeficitItem, User
from app.services.deficit import load_all_items
from app.services.sheets import SheetsClient
from app.storage.memory import UserStore
from app.utils.formatting import fmt_num
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)
router = Router(name="deficit")

# Bu routerdagi hamma narsa "mahsulotlarni ko'rish" ruxsatini talab qiladi (ro'yxatni hamma ko'ra oladi)
router.message.filter(HasPermission(Permission.VIEW_DEFICIT))
router.callback_query.filter(HasPermission(Permission.VIEW_DEFICIT))

KIND_ALL = "all"
KIND_DEFICIT = "def"
KINDS = (KIND_ALL, KIND_DEFICIT)


def _menu_kb(t: dict[str, str], user: User, kind: str):
    # Yuklab olish tugmasi faqat DOWNLOAD_REPORTS ruxsati borlarga ko'rinadi
    return deficit_menu_kb(t, can_download=has_permission(user.role, Permission.DOWNLOAD_REPORTS), kind=kind)


def _count_text(t: dict[str, str], items: list[DeficitItem], kind: str) -> str:
    if kind == KIND_ALL:
        return t["all_count_title"].format(count=len(items), deficit=sum(1 for i in items if i.is_deficit))
    return t["count_title"].format(count=len(items))


def _build_page_text(items: list[DeficitItem], page: int, total_pages: int,
                     total: int, t: dict[str, str], kind: str = KIND_DEFICIT) -> str:
    if kind == KIND_ALL:
        title = f"📦 <b>{t['all_total_count']}: {total} {t['unit']}</b>\n"
    else:
        title = f"🚨 <b>{t['total_count']}: {total} {t['unit']}</b>\n"
    header = (
        title
        + f"📄 {t['page']}: {page + 1}/{total_pages}\n"
        + "----------------------------------------\n\n"
    )
    body = "\n\n".join(
        f"{'⚠️' if i.is_deficit else '✅'} <b>{escape(i.name)}</b>\n"
        f"   {t['norm']}: {fmt_num(i.norm)} {t['unit']} | "
        f"{t['stock']}: {fmt_num(i.stock)} {t['unit']}"
        for i in items
    )
    return header + body


# Eski menyu tugmasi ("Tanqis mahsulotlar") Telegramda qolib ketgan bo'lsa ham ishlaydi
@router.message(F.text.in_(all_texts("btn_deficit") | all_texts("btn_deficit_legacy")))
async def show_inspect_menu(message: Message, t: dict[str, str]) -> None:
    """"Mahsulotlarni tekshirish": Barcha mahsulotlar / Tanqis mahsulotlar."""
    await message.answer(t["inspect_prompt"], reply_markup=inspect_menu_kb(t))


@router.callback_query(ProductCb.filter(F.kind == "menu"))
async def back_to_inspect_menu(callback: CallbackQuery, t: dict[str, str]) -> None:
    await callback.message.edit_text(t["inspect_prompt"], reply_markup=inspect_menu_kb(t))
    await callback.answer()


@router.callback_query(ProductCb.filter(F.kind.in_(KINDS)))
async def show_products(callback: CallbackQuery, callback_data: ProductCb, t: dict[str, str], user: User,
                        store: UserStore, sheets: SheetsClient) -> None:
    kind = callback_data.kind
    try:
        everything = await load_all_items(sheets)
    except Exception:
        logger.exception("Google Sheets'dan o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return

    # ikkala ro'yxat bitta o'qishdan olinadi, shuning uchun ular bir-biriga mos bo'ladi
    store.set_products(user.id, KIND_ALL, everything)
    store.set_products(user.id, KIND_DEFICIT, [i for i in everything if i.is_deficit])
    items = store.get_products(user.id, kind)

    if not items:
        await callback.answer(t["no_deficit"] if kind == KIND_DEFICIT else t["no_products"], show_alert=True)
        return

    await callback.message.edit_text(_count_text(t, items, kind), reply_markup=_menu_kb(t, user, kind))
    await callback.answer()


@router.callback_query(PageCb.filter(F.kind.in_(KINDS)))
async def show_page(callback: CallbackQuery, callback_data: PageCb, t: dict[str, str],
                    user: User, store: UserStore, settings: Settings) -> None:
    kind = callback_data.kind
    items = store.get_products(user.id, kind)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    chunk, page, total_pages = paginate(items, callback_data.page, settings.page_size)
    await callback.message.edit_text(
        _build_page_text(list(chunk), page, total_pages, len(items), t, kind),
        reply_markup=pagination_kb(page, total_pages, t, kind),
    )
    await callback.answer()


@router.callback_query(MenuCb.filter(F.action == "back"))
async def back_to_menu(callback: CallbackQuery, callback_data: MenuCb, t: dict[str, str],
                       user: User, store: UserStore) -> None:
    kind = callback_data.kind if callback_data.kind in KINDS else KIND_DEFICIT
    items = store.get_products(user.id, kind)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    await callback.message.edit_text(_count_text(t, items, kind), reply_markup=_menu_kb(t, user, kind))
    await callback.answer()


@router.callback_query(MenuCb.filter(F.action == "noop"))
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
