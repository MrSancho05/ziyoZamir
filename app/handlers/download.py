import asyncio
import logging

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import DownloadCb, MenuCb
from app.keyboards.inline import download_kb
from app.models import User
from app.services.reports.csv_report import build_deficit_csv
from app.services.reports.pdf import build_deficit_pdf
from app.storage.memory import UserStore

logger = logging.getLogger(__name__)
router = Router(name="download")

# Faqat "hisobotni yuklab olish" ruxsati borlar (admin, boss, sklad nazoratchisi)
router.callback_query.filter(HasPermission(Permission.DOWNLOAD_REPORTS))


@router.callback_query(MenuCb.filter(F.action == "download"))
async def open_download_menu(callback: CallbackQuery, t: dict[str, str]) -> None:
    await callback.message.edit_text(t["download_prompt"], reply_markup=download_kb(t))
    await callback.answer()


@router.callback_query(DownloadCb.filter(F.fmt == "pdf"))
async def download_pdf(callback: CallbackQuery, t: dict[str, str],
                       user: User, store: UserStore) -> None:
    items = store.get_deficit(user.id)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    await callback.answer()  # callback muddati tugab qolmasligi uchun darrov

    try:
        pdf_bytes = await asyncio.to_thread(build_deficit_pdf, items, t)
    except Exception:
        logger.exception("PDF yaratishda xatolik")
        await callback.message.answer(t["pdf_error"])
        return

    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=t["pdf_filename"]),
        caption=t["success_pdf"],
    )


@router.callback_query(DownloadCb.filter(F.fmt == "csv"))
async def download_csv(callback: CallbackQuery, t: dict[str, str],
                       user: User, store: UserStore) -> None:
    items = store.get_deficit(user.id)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return

    await callback.answer()
    await callback.message.answer_document(
        BufferedInputFile(build_deficit_csv(items, t), filename=t["csv_filename"]),
        caption=t["success_csv"],
    )
