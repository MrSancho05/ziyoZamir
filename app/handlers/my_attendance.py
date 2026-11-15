import logging
from datetime import date, datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import MyCb
from app.keyboards.inline import my_kb
from app.locales.texts import all_texts, month_name
from app.models import User
from app.services.attendance import AttendanceService
from app.services.payroll import fmt_money

logger = logging.getLogger(__name__)
router = Router(name="my_attendance")

# Ishchi faqat o'z ma'lumotini ko'radi
router.message.filter(HasPermission(Permission.VIEW_OWN_ATTENDANCE))
router.callback_query.filter(HasPermission(Permission.VIEW_OWN_ATTENDANCE))


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _dates(days: list[date]) -> str:
    return ", ".join(d.strftime("%d.%m") for d in days)


async def build_text(t: dict[str, str], lang: str, user: User, attendance: AttendanceService,
                     today: date, previous: bool) -> str:
    if not user.worker_name:
        return t["my_not_linked"]

    first = today.replace(day=1)
    if previous:
        last_prev = first - timedelta(days=1)
        year, month, until = last_prev.year, last_prev.month, last_prev
    else:
        year, month, until = today.year, today.month, today
    # Sarlavhada ishchining to'liq ismi (jadvaldagi ism) ko'rsatiladi
    title = t["my_title"].format(name=escape(user.worker_name), month=month_name(lang, month), year=year)

    rep = await attendance.month_payroll(year, month, until)
    stat = next((w for w in rep.stats.workers if _same(w.name, user.worker_name)), None)
    pay = next((p for p in rep.pays or [] if _same(p.name, user.worker_name)), None)
    if not rep.tab_found or (stat is None and pay is None):
        return f"{title}\n\n{t['my_none']}"

    lines = [title, "", t["my_recorded"].format(n=rep.stats.recorded_days)]
    if stat is not None:
        lines.append(t["my_came"].format(n=stat.came))
        if stat.absent:
            lines.append(t["my_absent"].format(n=stat.absent, dates=_dates(stat.absent_days)))
        if stat.excused:
            lines.append(t["my_excused"].format(n=stat.excused, dates=_dates(stat.excused_days)))
        if stat.saturday_days:
            lines.append(t["my_sat"].format(n=len(stat.saturday_days), dates=_dates(stat.saturday_days)))
    lines.append("")
    if pay is not None and pay.salary is not None:
        lines.append(t["my_pay"].format(salary=fmt_money(pay.salary), deduction=fmt_money(pay.deduction),
                                        days=pay.deduct_days, payable=fmt_money(pay.payable)))
    else:
        lines.append(t["my_pay_none"])
    return "\n".join(lines)


@router.message(F.text.in_(all_texts("btn_my")))
async def my_attendance(message: Message, t: dict[str, str], lang: str, user: User,
                        attendance: AttendanceService, settings: Settings) -> None:
    today = datetime.now(settings.timezone).date()
    try:
        text = await build_text(t, lang, user, attendance, today, previous=False)
    except Exception:
        logger.exception("Ishchi davomatini o'qishda xatolik")
        await message.answer(t["error"])
        return
    await message.answer(text, reply_markup=my_kb(t) if user.worker_name else None)


@router.callback_query(MyCb.filter())
async def my_attendance_switch(callback: CallbackQuery, callback_data: MyCb, t: dict[str, str],
                               lang: str, user: User, attendance: AttendanceService,
                               settings: Settings) -> None:
    today = datetime.now(settings.timezone).date()
    try:
        text = await build_text(t, lang, user, attendance, today, previous=callback_data.action == "prev")
    except Exception:
        logger.exception("Ishchi davomatini o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return
    await callback.message.edit_text(text, reply_markup=my_kb(t))
    await callback.answer()
