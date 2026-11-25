import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import DownloadCb, MenuCb
from app.keyboards.inline import download_kb
from app.models import User
from app.services.reports.csv_report import build_all_products_csv, build_deficit_csv
from app.services.reports.deficit_report import build_pdf as build_report_pdf
from app.services.reports.deficit_report import build_rows, build_xlsx
from app.services.warehouse import WarehouseService
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


@router.callback_query(DownloadCb.filter(F.fmt.in_({"pdf", "xlsx"})))
async def download_report(callback: CallbackQuery, callback_data: DownloadCb, t: dict[str, str], lang: str,
                          user: User, store: UserStore, settings: Settings, warehouse: WarehouseService) -> None:
    """📊 Excel / 📄 PDF: №, nom, kategoriya, birlik, qoldiq, min. qoldiq, yetishmayotgan miqdor."""
    kind = _kind(callback_data.kind)
    items = store.get_products(user.id, kind)
    if not items:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await callback.answer("⏳")  # callback muddati tugab qolmasligi uchun darrov

    try:
        catalog = await warehouse.catalog()
    except Exception:
        logger.exception("Katalogni o'qib bo'lmadi (kategoriya/birliksiz davom etiladi)")
        catalog = []
    rows = build_rows(items, catalog, lang)
    title = t["rep_title_all"] if kind == KIND_ALL else t["rep_title_def"]
    stamp = datetime.now(settings.timezone).strftime("%Y-%m-%d")
    xlsx = callback_data.fmt == "xlsx"
    try:
        # vaqt O'zbekiston vaqti (settings.timezone, Asia/Tashkent) bilan yoziladi
        data = await asyncio.to_thread(build_xlsx if xlsx else build_report_pdf, rows, t, title, settings.timezone)
    except ImportError:
        logger.exception("openpyxl o'rnatilmagan: pip install -r requirements.txt")
        await callback.message.answer(t["xlsx_missing"])
        return
    except Exception:
        logger.exception("Hisobot faylini yaratishda xatolik")
        await callback.message.answer(t["pdf_error"])
        return
    base = t["rep_file_all"] if kind == KIND_ALL else t["rep_file_def"]
    filename = f"{base}_{stamp}.{'xlsx' if xlsx else 'pdf'}"
    await callback.message.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=t["rep_caption"].format(n=len(rows), short=sum(1 for r in rows if r.short > 0)),
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
