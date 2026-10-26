import calendar
import logging
from datetime import date, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission, Role
from app.filters.permission import HasPermission
from app.keyboards.callbacks import AttCb
from app.keyboards.inline import (
    attendance_calendar_kb,
    attendance_edit_kb,
    attendance_kb,
    attendance_menu_kb,
    attendance_send_kb,
)
from app.locales.texts import all_texts, get_texts, month_name, weekday_name, weekday_short
from app.models import AttendanceDraft, User
from app.services.access import safe_send
from app.services.attendance import (
    AttendanceService,
    LayoutError,
    SheetChangedError,
    TemplateMissingError,
)
from app.services.attendance_grid import EXCUSED, MINUS, PLUS, is_markable_day, is_saturday
from app.services.attendance_report import format_daily, format_saturday
from app.storage.memory import UserStore
from app.storage.users import UserRepository
from app.utils.text import split_message

logger = logging.getLogger(__name__)
router = Router(name="attendance")

# Faqat admin va sklad nazoratchisi
router.message.filter(HasPermission(Permission.MARK_ATTENDANCE))
router.callback_query.filter(HasPermission(Permission.MARK_ATTENDANCE))

# Dushanba-juma: ⬜ -> ✅ -> ❌ -> 🟡 -> ✅ ...
_NEXT = {None: PLUS, PLUS: MINUS, MINUS: EXCUSED, EXCUSED: PLUS}
# Shanba (ixtiyoriy): ⬜ kelmadi <-> ✅ keldi
_NEXT_SAT = {None: PLUS, PLUS: None}

CAL_MONTHS_BACK = 24      # kalendarda necha oy orqaga
CAL_MONTHS_FORWARD = 12   # necha oy oldinga


def _today(settings: Settings) -> date:
    return datetime.now(settings.timezone).date()


def _is_sat(draft: AttendanceDraft) -> bool:
    return is_saturday(draft.day)


def _counts(draft: AttendanceDraft) -> tuple[int, int, int, int]:
    came = draft.marks.count(PLUS)
    absent = draft.marks.count(MINUS)
    excused = draft.marks.count(EXCUSED)
    return came, absent, excused, len(draft.marks) - came - absent - excused


def _text(t: dict[str, str], draft: AttendanceDraft) -> str:
    came, absent, excused, unmarked = _counts(draft)
    note = t["att_existing_note"] if draft.existing else ""
    if _is_sat(draft):
        return t["att_title_sat"].format(
            date=draft.day.strftime("%d.%m.%Y"), came=came, absent=len(draft.marks) - came, note=note)
    return t["att_title"].format(
        date=draft.day.strftime("%d.%m.%Y"), came=came, absent=absent,
        excused=excused, unmarked=unmarked, note=note)


async def _refresh(callback: CallbackQuery, t: dict[str, str], draft: AttendanceDraft) -> None:
    await callback.message.edit_text(_text(t, draft), reply_markup=attendance_kb(t, draft))
    await callback.answer()


async def _build_draft(day: date, today: date, t: dict[str, str],
                       attendance: AttendanceService) -> tuple[AttendanceDraft | None, str | None]:
    """Berilgan kun uchun qoralama. Xato bo'lsa (None, xabar matni).
    Bugun: kerak bo'lsa oy bloki yaratiladi. Boshqa kun: faqat mavjud blok o'qiladi."""
    try:
        data = await attendance.load_day(day) if day == today else await attendance.peek_day(day)
    except TemplateMissingError as exc:
        return None, t["att_no_template"].format(tab=escape(exc.tab))
    except LayoutError:
        return None, t["att_layout_error"]
    except Exception:
        logger.exception("Yo'qlama jadvalini o'qishda xatolik")
        return None, t["error"]

    if data is None:
        return None, t["att_no_block"].format(date=day.strftime("%d.%m.%Y"))
    names, marks = data
    if not names:
        return None, t["att_no_workers"]
    if is_saturday(day):
        marks = [PLUS if m == PLUS else None for m in marks]    # shanba: faqat kelganlar belgilanadi
    return AttendanceDraft(day=day, names=names, marks=marks,
                           existing=any(m is not None for m in marks)), None


async def _open_today(message: Message, t: dict[str, str], lang: str, user: User,
                      store: UserStore, attendance: AttendanceService, settings: Settings) -> None:
    day = _today(settings)
    if not is_markable_day(day):                       # faqat yakshanba
        await message.answer(t["att_weekend"].format(weekday=weekday_name(lang, day.weekday())))
        return
    draft, error = await _build_draft(day, day, t, attendance)
    if draft is None:
        await message.answer(error)
        return
    store.set_draft(user.id, draft)
    await message.answer(_text(t, draft), reply_markup=attendance_kb(t, draft))


# ---------------- menyu ----------------
@router.message(F.text.in_(all_texts("btn_attendance")))
async def open_from_menu(message: Message, t: dict[str, str]) -> None:
    await message.answer(t["att_menu_prompt"], reply_markup=attendance_menu_kb(t))


@router.callback_query(AttCb.filter(F.action == "menu"))
async def back_to_menu(callback: CallbackQuery, t: dict[str, str]) -> None:
    await callback.message.edit_text(t["att_menu_prompt"], reply_markup=attendance_menu_kb(t))
    await callback.answer()


@router.callback_query(AttCb.filter(F.action == "open"))
async def open_today(callback: CallbackQuery, t: dict[str, str], lang: str, user: User,
                     store: UserStore, attendance: AttendanceService,
                     settings: Settings) -> None:
    """"Bugungi yo'qlamani o'tkazish" tugmasi va soat 10:00 dagi eslatmadagi tugma."""
    await callback.answer()
    await _open_today(callback.message, t, lang, user, store, attendance, settings)


# ---------------- istalgan kunni o'zgartirish: kalendar ----------------
def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


@router.callback_query(AttCb.filter(F.action == "noop"))
async def calendar_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(AttCb.filter(F.action == "past"))
async def open_calendar(callback: CallbackQuery, t: dict[str, str], lang: str, settings: Settings,
                        attendance: AttendanceService) -> None:
    """"Istalgan kun yo'qlamasini o'zgartirish": joriy oy kalendari."""
    today = _today(settings)
    await _show_calendar(callback, t, lang, today, today.year, today.month, attendance)


@router.callback_query(AttCb.filter(F.action == "cal"))
async def navigate_calendar(callback: CallbackQuery, callback_data: AttCb, t: dict[str, str],
                            lang: str, settings: Settings, attendance: AttendanceService) -> None:
    year, month = divmod(callback_data.idx, 100)
    today = _today(settings)
    offset = (year * 12 + month) - (today.year * 12 + today.month)
    if not 1 <= month <= 12 or not -CAL_MONTHS_BACK <= offset <= CAL_MONTHS_FORWARD:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await _show_calendar(callback, t, lang, today, year, month, attendance)


async def _show_calendar(callback: CallbackQuery, t: dict[str, str], lang: str, today: date,
                         year: int, month: int, attendance: AttendanceService) -> None:
    days_in_month = calendar.monthrange(year, month)[1]
    try:
        statuses = await attendance.day_statuses(
            [date(year, month, d) for d in range(1, days_in_month + 1)])
    except Exception:
        logger.exception("Kalendar uchun kunlar holatini o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return

    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for d in week:
            if d == 0:
                row.append(("·", "noop", 0))
                continue
            dt = date(year, month, d)
            if not is_markable_day(dt):                     # yakshanba
                row.append(("·", "noop", 0))
                continue
            status = statuses[dt]
            if status == "nosheet":
                label = f"🚫{d}"
            elif status == "rec":
                label = f"✅{d}"
            elif is_saturday(dt):
                label = f"🛠{d}"
            elif dt <= today:
                label = f"⚠️{d}"
            else:
                label = str(d)
            row.append((label, "day", int(dt.strftime("%Y%m%d"))))
        weeks.append(row)

    py, pm = _shift_month(year, month, -1)
    ny, nm = _shift_month(year, month, 1)
    offset = (year * 12 + month) - (today.year * 12 + today.month)
    prev_ym = py * 100 + pm if offset - 1 >= -CAL_MONTHS_BACK else None
    next_ym = ny * 100 + nm if offset + 1 <= CAL_MONTHS_FORWARD else None

    await callback.message.edit_text(
        t["att_past_title"],
        reply_markup=attendance_calendar_kb(
            t, f"{month_name(lang, month)} {year}", [weekday_short(lang, i) for i in range(7)],
            weeks, prev_ym, next_ym),
    )
    await callback.answer()


@router.callback_query(AttCb.filter(F.action == "day"))
async def open_chosen_day(callback: CallbackQuery, callback_data: AttCb, t: dict[str, str],
                          user: User, store: UserStore, attendance: AttendanceService,
                          settings: Settings) -> None:
    ymd = callback_data.idx
    try:
        day = date(ymd // 10000, ymd // 100 % 100, ymd % 100)
    except ValueError:
        await callback.answer(t["no_data"], show_alert=True)
        return
    if not is_markable_day(day):
        await callback.answer(t["no_data"], show_alert=True)
        return

    draft, error = await _build_draft(day, _today(settings), t, attendance)
    if draft is None:
        await callback.answer(error, show_alert=True)
        return
    store.set_draft(user.id, draft)
    await _refresh(callback, t, draft)


# ---------------- belgilash, saqlash, tahrirlash, yuborish ----------------
@router.callback_query(AttCb.filter(F.action == "t"))
async def toggle(callback: CallbackQuery, callback_data: AttCb, t: dict[str, str],
                 user: User, store: UserStore) -> None:
    draft = store.get_draft(user.id)
    idx = callback_data.idx
    if draft is None or draft.saved or not 0 <= idx < len(draft.marks):
        await callback.answer(t["no_data"], show_alert=True)
        return
    draft.marks[idx] = (_NEXT_SAT if _is_sat(draft) else _NEXT)[draft.marks[idx]]
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
    saturday = _is_sat(draft)
    if unmarked and not saturday:       # shanba: belgilanmaganlar = kelmadi, bo'sh qoladi
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
    date_str = draft.day.strftime("%d.%m.%Y")
    text = (t["att_saved_sat"].format(date=date_str, came=came) if saturday else
            t["att_saved"].format(date=date_str, came=came, absent=absent, excused=excused))
    await callback.message.edit_text(text, reply_markup=attendance_send_kb(t))
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
        bt = get_texts(boss.lang)
        text = (format_saturday(bt, draft.day, came, user.full_name) if _is_sat(draft)
                else format_daily(bt, draft.day, came, unexcused, excused, user.full_name))
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
