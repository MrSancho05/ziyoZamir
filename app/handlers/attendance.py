import calendar
import logging
from datetime import date, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import AttCb
from app.keyboards.inline import (
    attendance_calendar_kb,
    attendance_kb,
    attendance_menu_kb,
)
from app.locales.texts import all_texts, month_name, weekday_name, weekday_short
from app.models import AttendanceDraft, User
from app.services.attendance import (
    AttendanceService,
    LayoutError,
    SheetChangedError,
    TemplateMissingError,
)
from app.services.attendance_grid import EXCUSED, MINUS, PLUS, is_markable_day, is_saturday
from app.services.boss_reports import attendance_day_text, send_to_bosses
from app.services.worker_notify import notify_workers
from app.storage.attendance_store import AttendanceStore
from app.storage.memory import UserStore
from app.storage.users import UserRepository

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


def _now_ts(settings: Settings) -> int:
    return int(datetime.now(settings.timezone).timestamp())


def _is_sat(draft: AttendanceDraft) -> bool:
    return is_saturday(draft.day)


def _counts(draft: AttendanceDraft) -> tuple[int, int, int, int]:
    came = draft.marks.count(PLUS)
    absent = draft.marks.count(MINUS)
    excused = draft.marks.count(EXCUSED)
    return came, absent, excused, len(draft.marks) - came - absent - excused


def _text(t: dict[str, str], draft: AttendanceDraft, sent: bool) -> str:
    came, absent, excused, unmarked = _counts(draft)
    note = t["att_existing_note"] if draft.existing else ""
    if sent:
        note += t["att_sent_note"]
    if _is_sat(draft):
        return t["att_title_sat"].format(
            date=draft.day.strftime("%d.%m.%Y"), came=came, absent=len(draft.marks) - came, note=note)
    return t["att_title"].format(
        date=draft.day.strftime("%d.%m.%Y"), came=came, absent=absent,
        excused=excused, unmarked=unmarked, note=note)


async def _refresh(callback: CallbackQuery, t: dict[str, str], draft: AttendanceDraft,
                   att_store: AttendanceStore) -> None:
    """Oynani qayta chizadi. Shu kun hisoboti allaqachon bossga yuborilgan bo'lsa, «Bossga yuborish» yashiriladi."""
    sent = await att_store.is_sent(draft.day)
    await callback.message.edit_text(_text(t, draft, sent),
                                     reply_markup=attendance_kb(t, draft, show_send=not sent))
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
                      store: UserStore, attendance: AttendanceService, settings: Settings,
                      att_store: AttendanceStore) -> None:
    day = _today(settings)
    if not is_markable_day(day):                       # faqat yakshanba
        await message.answer(t["att_weekend"].format(weekday=weekday_name(lang, day.weekday())))
        return
    draft, error = await _build_draft(day, day, t, attendance)
    if draft is None:
        await message.answer(error)
        return
    store.set_draft(user.id, draft)
    sent = await att_store.is_sent(day)
    await message.answer(_text(t, draft, sent), reply_markup=attendance_kb(t, draft, show_send=not sent))


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
                     settings: Settings, att_store: AttendanceStore) -> None:
    """"Bugungi yo'qlamani o'tkazish" tugmasi va soat 10:00 dagi eslatmadagi tugma."""
    await callback.answer()
    await _open_today(callback.message, t, lang, user, store, attendance, settings, att_store)


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
                          settings: Settings, att_store: AttendanceStore) -> None:
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
    draft.from_calendar = True       # "Bekor qilish" kalendarga qaytaradi
    store.set_draft(user.id, draft)
    await _refresh(callback, t, draft, att_store)


# ---------------- belgilash (darhol saqlanadi), yuborish, bekor qilish ----------------
async def _persist(callback: CallbackQuery, t: dict[str, str], user: User, draft: AttendanceDraft,
                   changes: dict[int, str | None], attendance: AttendanceService,
                   att_store: AttendanceStore, settings: Settings) -> bool:
    """O'zgargan belgilarni Google Sheets ga va bazaga darhol yozadi, so'ng oynadagi nusxani yangilaydi.
    Jadvalga yozib bo'lmasa, oynadagi belgi ham o'zgarmaydi (False qaytaradi)."""
    try:
        await attendance.set_marks(draft.day, draft.names, changes)
    except SheetChangedError:
        await callback.answer(t["att_sheet_changed"], show_alert=True)
        return False
    except Exception:
        logger.exception("Yo'qlamani jadvalga yozishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return False

    for idx, mark in changes.items():
        draft.marks[idx] = mark
    try:
        await att_store.record_marks(draft.day, [(draft.names[i], m) for i, m in changes.items()],
                                     user.id, _now_ts(settings))
    except Exception:
        # Jadval asosiy manba: bazaga yozilmasa ham yo'qlama yo'qolmaydi
        logger.exception("Yo'qlamani bazaga yozishda xatolik")
    return True


async def _after_edit(bot: Bot, repo: UserRepository, att_store: AttendanceStore, draft: AttendanceDraft,
                      changes: dict[int, str | None]) -> None:
    """Hisobot bossga yuborilgandan keyingi o'zgarishlar bossga QAYTA yuborilmaydi.
    Faqat holati o'zgargan ishchiga yangilangan holat haqida xabar boradi."""
    if not await att_store.is_sent(draft.day):
        return
    idxs = sorted(changes)
    await notify_workers(bot, repo, att_store, draft.day,
                         [draft.names[i] for i in idxs], [draft.marks[i] for i in idxs])


@router.callback_query(AttCb.filter(F.action == "t"))
async def toggle(callback: CallbackQuery, callback_data: AttCb, t: dict[str, str], user: User,
                 store: UserStore, attendance: AttendanceService, att_store: AttendanceStore,
                 repo: UserRepository, bot: Bot, settings: Settings) -> None:
    async with store.draft_lock(user.id):          # tez-tez bosilganda belgilar chalkashmasligi uchun
        draft = store.get_draft(user.id)
        idx = callback_data.idx
        if draft is None or not 0 <= idx < len(draft.marks):
            await callback.answer(t["no_data"], show_alert=True)
            return
        changes = {idx: (_NEXT_SAT if _is_sat(draft) else _NEXT)[draft.marks[idx]]}
        if not await _persist(callback, t, user, draft, changes, attendance, att_store, settings):
            return
        await _refresh(callback, t, draft, att_store)
    await _after_edit(bot, repo, att_store, draft, changes)


@router.callback_query(AttCb.filter(F.action == "all"))
async def mark_all(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
                   attendance: AttendanceService, att_store: AttendanceStore, repo: UserRepository,
                   bot: Bot, settings: Settings) -> None:
    async with store.draft_lock(user.id):
        draft = store.get_draft(user.id)
        if draft is None:
            await callback.answer(t["no_data"], show_alert=True)
            return
        # Faqat hali belgilanmaganlar "keldi" bo'ladi, ❌ va 🟡 lar o'zgarmaydi
        changes = {i: PLUS for i, m in enumerate(draft.marks) if m is None}
        if not changes:
            await callback.answer()
            return
        if not await _persist(callback, t, user, draft, changes, attendance, att_store, settings):
            return
        await _refresh(callback, t, draft, att_store)
    await _after_edit(bot, repo, att_store, draft, changes)


@router.callback_query(AttCb.filter(F.action == "send"))
async def send_to_boss(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
                       attendance: AttendanceService, att_store: AttendanceStore,
                       repo: UserRepository, bot: Bot, settings: Settings) -> None:
    """"Bossga yuborish": har bir kun hisoboti bossga faqat BIR marta (qo'lda yoki avtomatik) boradi."""
    draft = store.get_draft(user.id)
    if draft is None:
        await callback.answer(t["no_data"], show_alert=True)
        return

    async with att_store.send_lock:
        if await att_store.is_sent(draft.day):
            await callback.answer(t["att_already_sent"], show_alert=True)
            await _refresh(callback, t, draft, att_store)        # tugma yo'qoladi
            return

        # Boshqa xodim ham belgilagan bo'lishi mumkin: yuborishdan oldin jadvaldagi so'nggi holat olinadi
        try:
            data = await attendance.peek_day(draft.day)
        except Exception:
            logger.exception("Yo'qlamani yuborishdan oldin o'qishda xatolik")
            await callback.answer(t["error"], show_alert=True)
            return
        if data is not None and data[0] == draft.names:
            draft.marks = ([PLUS if m == PLUS else None for m in data[1]] if _is_sat(draft)
                           else list(data[1]))
        names, marks = draft.names, draft.marks

        unmarked = marks.count(None)
        if unmarked and not _is_sat(draft):       # shanba: belgilanmaganlar = kelmadi
            await callback.answer(t["att_not_all"].format(count=unmarked), show_alert=True)
            return

        delivered = await send_to_bosses(
            bot, repo, lambda bt, lang: attendance_day_text(bt, draft.day, names, marks, user.full_name))
        if delivered == 0:
            await callback.answer(t["att_no_boss"], show_alert=True)
            return
        await att_store.mark_sent(draft.day, _now_ts(settings), auto=False, by=user.id)

    await _refresh(callback, t, draft, att_store)
    await notify_workers(bot, repo, att_store, draft.day, names, marks)
    await callback.message.answer(t["att_sent"].format(date=draft.day.strftime("%d.%m.%Y"), count=delivered))


@router.callback_query(AttCb.filter(F.action == "cancel"))
async def cancel(callback: CallbackQuery, t: dict[str, str], lang: str, store: UserStore,
                 user: User, settings: Settings, attendance: AttendanceService) -> None:
    """"Bekor qilish": o'zgarishlar allaqachon saqlangan, shuning uchun oyna shunchaki yopiladi va
    oldingi menyuga (kalendardan kirilgan bo'lsa - kalendarga) qaytiladi."""
    draft = store.clear_draft(user.id)
    if draft is not None and draft.from_calendar:
        await _show_calendar(callback, t, lang, _today(settings), draft.day.year, draft.day.month,
                             attendance)
        return
    await back_to_menu(callback, t)
