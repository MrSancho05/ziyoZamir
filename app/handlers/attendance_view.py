import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import ViewCb
from app.keyboards.inline import view_menu_kb
from app.locales.texts import all_texts, month_name, weekday_name
from app.services.attendance import AttendanceService
from app.services.attendance_grid import EXCUSED, MINUS, PLUS, is_markable_day, is_saturday
from app.services.attendance_report import (
    format_daily,
    format_month,
    format_period,
    format_saturday,
)
from app.utils.text import split_message

logger = logging.getLogger(__name__)
router = Router(name="attendance_view")

# Faqat boss va admin
router.message.filter(HasPermission(Permission.VIEW_ATTENDANCE))
router.callback_query.filter(HasPermission(Permission.VIEW_ATTENDANCE))


async def _reply(callback: CallbackQuery, text: str) -> None:
    for part in split_message(text):
        await callback.message.answer(part)


@router.message(F.text.in_(all_texts("btn_check")))
async def open_menu(message: Message, t: dict[str, str]) -> None:
    await message.answer(t["view_prompt"], reply_markup=view_menu_kb(t))


@router.callback_query(ViewCb.filter(F.action == "today"))
async def view_today(callback: CallbackQuery, t: dict[str, str], lang: str, settings: Settings,
                     attendance: AttendanceService) -> None:
    await callback.answer()
    day = datetime.now(settings.timezone).date()
    if not is_markable_day(day):
        await callback.message.answer(t["att_weekend"].format(weekday=weekday_name(lang, day.weekday())))
        return
    try:
        data = await attendance.peek_day(day)
    except Exception:
        logger.exception("Bugungi davomatni o'qishda xatolik")
        await callback.message.answer(t["error"])
        return

    if is_saturday(day):                    # shanba: faqat ishlaganlar
        came = [n for n, m in zip(*data) if m == PLUS] if data else []
        if not came:
            await callback.message.answer(t["view_sat_none"].format(date=day.strftime("%d.%m.%Y")))
        else:
            await _reply(callback, format_saturday(t, day, came))
        return

    if data is None or all(m is None for m in data[1]):
        await callback.message.answer(t["view_today_none"].format(date=day.strftime("%d.%m.%Y")))
        return

    pairs = list(zip(*data))
    text = format_daily(
        t, day,
        came=[n for n, m in pairs if m == PLUS],
        unexcused=[n for n, m in pairs if m == MINUS],
        excused=[n for n, m in pairs if m == EXCUSED],
    )
    unmarked = [n for n, m in pairs if m is None]
    if unmarked:
        text += "\n\n" + t["rep_unmarked_title"] + "\n" + ", ".join(escape(n) for n in unmarked)
    await _reply(callback, text)


@router.callback_query(ViewCb.filter(F.action == "week"))
async def view_week(callback: CallbackQuery, t: dict[str, str], lang: str, settings: Settings,
                    attendance: AttendanceService) -> None:
    """O'tgan hafta: joriy haftadan oldingi dushanba-yakshanba."""
    await callback.answer()
    today = datetime.now(settings.timezone).date()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=6)
    try:
        stats = await attendance.period_stats(start, end)
    except Exception:
        logger.exception("Haftalik davomatni o'qishda xatolik")
        await callback.message.answer(t["error"])
        return
    title = t["rep_weekly_title"].format(start=start.strftime("%d.%m"), end=end.strftime("%d.%m.%Y"))
    await _reply(callback, format_period(t, lang, title, stats))


@router.callback_query(ViewCb.filter(F.action == "month"))
async def view_month(callback: CallbackQuery, t: dict[str, str], lang: str, settings: Settings,
                     attendance: AttendanceService) -> None:
    """Shu oy: oy boshidan bugungacha, maosh hisobi bilan."""
    await callback.answer()
    today = datetime.now(settings.timezone).date()
    try:
        report = await attendance.month_payroll(today.year, today.month, today)
    except Exception:
        logger.exception("Oylik davomatni o'qishda xatolik")
        await callback.message.answer(t["error"])
        return
    title = t["rep_month_view_title"].format(
        month=month_name(lang, today.month), year=today.year,
        start=today.replace(day=1).strftime("%d.%m"), end=today.strftime("%d.%m.%Y"))
    await _reply(callback, format_month(t, lang, title, report))
