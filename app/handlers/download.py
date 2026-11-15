import asyncio
import logging

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import DownloadCb, MenuCb
from app.keyboards.inline import download_kb
from app.models import User
from app.services.reports.csv_report import build_all_products_csv, build_deficit_csv
from app.services.reports.pdf import build_all_products_pdf, build_deficit_pdf
from app.storage.memory import UserStore

logger = logging.getLogger(__name__)
router = Router(name="download")

# Faqat "hisobotni yuklab olish" ruxsati borlar (admin, boss, sklad nazoratchisi).
# Ruxsatsiz rollar (ishchi) bu tugmalarni umuman ko'rmaydi; eski xabardagi tugma bosilsa ham rad etiladi.
router.callback_query.filter(HasPermission(Permission.DOWNLOAD_REPORTS))

KIND_ALL = "all"
KIND_DEFICIT = "def"


def _kind(value: str) -> str:
    return KIND_ALL if value == KIND_ALL else KIND_DEFICIT


@router.callback_query(MenuCb.filter(F.action == "download"))
async def open_download_menu(callback: CallbackQuery, callback_data: MenuCb, t: dict[str, str]) -> None:
    kind = _kind(callback_data.kind)
    prompt = t["all_download_prompt"] if kind == KIND_ALL else t["download_prompt"]
    await callback.message.edit_text(prompt, reply_markup=download_kb(t, kind))
    await callback.answer()


@router.callback_query(DownloadCb.filter(F.fmt == "pdf"))
async def download_pdf(callback: CallbackQuery, callback_data: DownloadCb, t: dict[str, str],
                       user: User, store: UserStore, settings: Settings) -> None:
    kind = _kind(callback_data.kind)
    items = store.get_products(user.id, kind)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    await callback.answer()  # callback muddati tugab qolmasligi uchun darrov

    build = build_all_products_pdf if kind == KIND_ALL else build_deficit_pdf
    try:
        # PDF dagi vaqt O'zbekiston vaqti (settings.timezone, standart Asia/Tashkent) bilan yoziladi
        pdf_bytes = await asyncio.to_thread(build, items, t, settings.timezone)
    except Exception:
        logger.exception("PDF yaratishda xatolik")
        await callback.message.answer(t["pdf_error"])
        return

    filename = t["all_pdf_filename"] if kind == KIND_ALL else t["pdf_filename"]
    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=filename),
        caption=t["success_pdf"],
    )


@router.callback_query(DownloadCb.filter(F.fmt == "csv"))
async def download_csv(callback: CallbackQuery, callback_data: DownloadCb, t: dict[str, str],
                       user: User, store: UserStore) -> None:
    kind = _kind(callback_data.kind)
    items = store.get_products(user.id, kind)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    await callback.answer()
    build = build_all_products_csv if kind == KIND_ALL else build_deficit_csv
    filename = t["all_csv_filename"] if kind == KIND_ALL else t["csv_filename"]
    await callback.message.answer_document(
        BufferedInputFile(build(items, t), filename=filename),
        caption=t["success_csv"],
    )
