import logging
from datetime import date, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission, Role
from app.filters.permission import HasPermission
from app.keyboards.callbacks import AttCb
from app.keyboards.inline import attendance_edit_kb, attendance_kb, attendance_send_kb
from app.locales.texts import all_texts, get_texts, weekday_name
from app.models import AttendanceDraft, User
from app.services.access import safe_send
from app.services.attendance import (
    AttendanceService,
    LayoutError,
    SheetChangedError,
    TemplateMissingError,
)
from app.services.attendance_grid import EXCUSED, MINUS, PLUS, is_workday
from app.services.attendance_report import format_daily
from app.storage.memory import UserStore
from app.storage.users import UserRepository
from app.utils.text import split_message

logger = logging.getLogger(__name__)
router = Router(name="attendance")

# Faqat admin va sklad nazoratchisi
router.message.filter(HasPermission(Permission.MARK_ATTENDANCE))
router.callback_query.filter(HasPermission(Permission.MARK_ATTENDANCE))

# Tugma bosilganda holat aylanadi: ⬜ -> ✅ -> ❌ -> 🟡 -> ✅ ...
_NEXT = {None: PLUS, PLUS: MINUS, MINUS: EXCUSED, EXCUSED: PLUS}


def _counts(draft: AttendanceDraft) -> tuple[int, int, int, int]:
    came = draft.marks.count(PLUS)
    absent = draft.marks.count(MINUS)
    excused = draft.marks.count(EXCUSED)
    return came, absent, excused, len(draft.marks) - came - absent - excused


def _text(t: dict[str, str], draft: AttendanceDraft) -> str:
    came, absent, excused, unmarked = _counts(draft)
    return t["att_title"].format(
        date=draft.day.strftime("%d.%m.%Y"), came=came, absent=absent,
        excused=excused, unmarked=unmarked,
        note=t["att_existing_note"] if draft.existing else "",
    )


async def _refresh(callback: CallbackQuery, t: dict[str, str], draft: AttendanceDraft) -> None:
    await callback.message.edit_text(_text(t, draft), reply_markup=attendance_kb(t, draft))
    await callback.answer()


async def _open_attendance(message: Message, t: dict[str, str], lang: str, user: User,
                           store: UserStore, attendance: AttendanceService,
                           settings: Settings) -> None:
    day: date = datetime.now(settings.timezone).date()

    if not is_workday(day):
        await message.answer(t["att_weekend"].format(weekday=weekday_name(lang, day.weekday())))
        return

    try:
        names, marks = await attendance.load_day(day)
    except TemplateMissingError as exc:
        await message.answer(t["att_no_template"].format(tab=escape(exc.tab)))
        return
    except LayoutError:
        await message.answer(t["att_layout_error"])
        return
    except Exception:
        logger.exception("Yo'qlama jadvalini o'qishda xatolik")
        await message.answer(t["error"])
        return

    if not names:
        await message.answer(t["att_no_workers"])
        return

    draft = AttendanceDraft(day=day, names=names, marks=marks,
                            existing=any(m is not None for m in marks))
    store.set_draft(user.id, draft)
    await message.answer(_text(t, draft), reply_markup=attendance_kb(t, draft))


@router.message(F.text.in_(all_texts("btn_attendance")))
async def open_from_menu(message: Message, t: dict[str, str], lang: str, user: User,
                         store: UserStore, attendance: AttendanceService,
                         settings: Settings) -> None:
    await _open_attendance(message, t, lang, user, store, attendance, settings)


@router.callback_query(AttCb.filter(F.action == "open"))
async def open_from_reminder(callback: CallbackQuery, t: dict[str, str], lang: str, user: User,
                             store: UserStore, attendance: AttendanceService,
                             settings: Settings) -> None:
    """Soat 10:00 dagi eslatmadagi "Yo'qlamani boshlash" tugmasi."""
    await callback.answer()
    await _open_attendance(callback.message, t, lang, user, store, attendance, settings)


@router.callback_query(AttCb.filter(F.action == "t"))
async def toggle(callback: CallbackQuery, callback_data: AttCb, t: dict[str, str],
                 user: User, store: UserStore) -> None:
    draft = store.get_draft(user.id)
    idx = callback_data.idx
    if draft is None or draft.saved or not 0 <= idx < len(draft.marks):
        await callback.answer(t["no_data"], show_alert=True)
        return
    draft.marks[idx] = _NEXT[draft.marks[idx]]
    await _refresh(callback, t, draft)


@router.callback_query(AttCb.filter(F.action == "all"))
async def mark_all(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore) -> None:
    draft = store.get_draft(user.id)
    if draft is None or draft.saved:
        await callback.answer(t["no_data"], show_alert=True)
        return
    if all(m is not None for m in draft.marks):
        await callback.answer()
        return
    # Faqat hali belgilanmaganlar "keldi" bo'ladi, ❌ va 🟡 lar o'zgarmaydi
    draft.marks = [PLUS if m is None else m for m in draft.marks]
    await _refresh(callback, t, draft)


@router.callback_query(AttCb.filter(F.action == "save"))
async def save(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
               attendance: AttendanceService) -> None:
    draft = store.get_draft(user.id)
    if draft is None or draft.saved:
        await callback.answer(t["no_data"], show_alert=True)
        return

    came, absent, excused, unmarked = _counts(draft)
    if unmarked:
        await callback.answer(t["att_not_all"].format(count=unmarked), show_alert=True)
        return

    try:
        await attendance.save_day(draft.day, draft.names, draft.marks)
    except SheetChangedError:
        await callback.answer(t["att_sheet_changed"], show_alert=True)
        return
    except Exception:
        logger.exception("Yo'qlamani jadvalga yozishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return

    draft.saved = True
    draft.sent = False
    await callback.message.edit_text(
        t["att_saved"].format(date=draft.day.strftime("%d.%m.%Y"),
                              came=came, absent=absent, excused=excused),
        reply_markup=attendance_send_kb(t),
    )
    await callback.answer()


@router.callback_query(AttCb.filter(F.action == "edit"))
async def edit_saved(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore) -> None:
    """Saqlangan (yoki bossga yuborilgan) yo'qlamani qaytadan tahrirlash."""
    draft = store.get_draft(user.id)
    if draft is None or not draft.saved:
        await callback.answer(t["no_data"], show_alert=True)
        return
    draft.saved = False
    draft.sent = False        # o'zgartirilgach, hisobotni qayta yuborish mumkin
    draft.existing = True
    await _refresh(callback, t, draft)


@router.callback_query(AttCb.filter(F.action == "send"))
async def send_to_boss(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
                       repo: UserRepository, bot: Bot) -> None:
    draft = store.get_draft(user.id)
    if draft is None or not draft.saved:
        await callback.answer(t["no_data"], show_alert=True)
        return
    if draft.sent:
        await callback.answer(t["att_already_sent"], show_alert=True)
        return

    pairs = list(zip(draft.names, draft.marks))
    came = [n for n, m in pairs if m == PLUS]
    unexcused = [n for n, m in pairs if m == MINUS]
    excused = [n for n, m in pairs if m == EXCUSED]

    delivered = 0
    for boss in await repo.list_by_role(Role.BOSS):
        text = format_daily(get_texts(boss.lang), draft.day, came, unexcused, excused, user.full_name)
        ok = True
        for part in split_message(text):
            ok = await safe_send(bot, boss.id, part) and ok
        delivered += ok

    if delivered == 0:
        await callback.answer(t["att_no_boss"], show_alert=True)
        return

    draft.sent = True
    await callback.message.edit_text(
        t["att_sent"].format(date=draft.day.strftime("%d.%m.%Y"), count=delivered),
        reply_markup=attendance_edit_kb(t),
    )
    await callback.answer()
