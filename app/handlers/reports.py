"""Boss/Admin hisobotlari: "📄 Batafsil" <-> "🔼 Qisqacha" va yig'ma hisobot bo'limlari.

Xabar HAR DOIM joyida tahrirlanadi (edit_text) — yangi xabar yuborilmaydi. Matnlar yuborish paytida
bazaga (report_cache) saqlangan, shuning uchun bot qayta ishga tushsa ham tugmalar ishlaydi."""
from __future__ import annotations

import logging
from typing import Dict

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.keyboards.callbacks import RepCb
from app.keyboards.inline import digest_kb, report_toggle_kb
from app.storage.report_cache import ReportCache

logger = logging.getLogger(__name__)
router = Router(name="reports")


@router.callback_query(RepCb.filter())
async def switch_section(callback: CallbackQuery, callback_data: RepCb, t: Dict[str, str], lang: str,
                         report_cache: ReportCache) -> None:
    sections = await report_cache.get(callback_data.k, callback_data.p, lang)
    if not sections or callback_data.s not in sections:
        await callback.answer(t["rp_expired"], show_alert=True)
        return
    if callback_data.k in ("w", "m"):
        markup = digest_kb(t, callback_data.k, callback_data.p, callback_data.s)
    else:
        markup = report_toggle_kb(t, callback_data.k, callback_data.p, callback_data.s)
    try:
        await callback.message.edit_text(sections[callback_data.s], reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            logger.warning("Hisobotni tahrirlab bo'lmadi: %s", exc)
    await callback.answer()
