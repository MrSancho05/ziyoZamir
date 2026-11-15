import calendar
import logging
from datetime import date, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import ShopCb
from app.keyboards.inline import (
    shop_calendar_kb,
    shop_adjust_menu_kb,
    shop_cancel_kb,
    shop_edit_kb,
    shop_menu_kb,
    shop_saved_kb,
)
from app.locales.texts import all_texts, menu_texts, month_name, weekday_short
from app.models import User
from app.services.attendance import LayoutError, TemplateMissingError
from app.services.boss_reports import format_shop_daily, send_to_bosses
from app.services.payroll import fmt_money
from app.services.shop import NegativeResultError, NoBlockError, NotEnteredError, ShopService
from app.services.shop_grid import parse_shop_amount
from app.storage.memory import UserStore
from app.storage.pending import PendingReports
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)
router = Router(name="shop")

# Faqat admin va sklad nazoratchisi
router.message.filter(HasPermission(Permission.MARK_EXPENSES))
router.callback_query.filter(HasPermission(Permission.MARK_EXPENSES))

CAL_MONTHS_BACK = 24
CAL_MONTHS_FORWARD = 12
MAX_AMOUNT = 10**12


def _today(settings: Settings) -> date:
    return datetime.now(settings.timezone).date()


def _now_ts(settings: Settings) -> int:
    return int(datetime.now(settings.timezone).timestamp())


def _ymd(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


def _from_ymd(ymd: int) -> date | None:
    try:
        return date(ymd // 10000, ymd // 100 % 100, ymd % 100)
    except ValueError:
        return None


class AwaitingAmount(BaseFilter):
    """Foydalanuvchi summa kiritishi kutilayotgan bo'lsa (va matn menyu tugmasi bo'lmasa)."""

    async def __call__(self, message: Message, user: User | None = None,
                       store: UserStore | None = None) -> bool:
        if user is None or store is None or not message.text or message.text in menu_texts():
            return False
        return store.get_awaiting(user.id) is not None


async def _prompt(target: Message, t: dict[str, str], day: date, shop: ShopService,
                  user: User, store: UserStore, edit: bool) -> None:
    """Summa kiritishni so'raydi (yoki xabarni shu matnga almashtiradi)."""
    try:
        found = await shop.get_day(day)
    except Exception:
        logger.exception("Do'kon jadvalini o'qishda xatolik")
        await target.answer(t["error"])
        return
    current = (fmt_money(found[1]) + " so'm" if found and found[0] and found[1] is not None
               else t["shop_current_none"])
    store.set_awaiting(user.id, "shop", _ymd(day))
    text = t["shop_prompt"].format(date=day.strftime("%d.%m.%Y"), current=current)
    if edit:
        await target.edit_text(text, reply_markup=shop_cancel_kb(t))
    else:
        await target.answer(text, reply_markup=shop_cancel_kb(t))


async def _today_entered(target: Message, t: dict[str, str], shop: ShopService,
                         settings: Settings) -> bool | None:
    """Bugungi xarajat kiritilganmi (yakshanba: False). Jadvalni o'qib bo'lmasa None (xabar yuboriladi)."""
    day = _today(settings)
    if day.weekday() == 6:
        return False
    try:
        return await shop.is_entered(day)
    except Exception:
        logger.exception("Do'kon jadvalini o'qishda xatolik")
        await target.answer(t["error"])
        return None


async def _show_adjust_menu(target: Message, t: dict[str, str], day: date, amount: int,
                            edit: bool) -> None:
    text = t["shop_adjust_menu"].format(date=day.strftime("%d.%m.%Y"), current=fmt_money(amount))
    kb = shop_adjust_menu_kb(t, _ymd(day))
    if edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ---------------- menyu ----------------
@router.message(F.text.in_(all_texts("btn_shop")))
async def open_menu(message: Message, t: dict[str, str], user: User, store: UserStore,
                    shop: ShopService, settings: Settings) -> None:
    store.clear_awaiting(user.id)
    entered = await _today_entered(message, t, shop, settings)
    if entered is None:
        return
    await message.answer(t["shop_menu_prompt"], reply_markup=shop_menu_kb(t, entered))


@router.callback_query(ShopCb.filter(F.action == "menu"))
async def back_to_menu(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
                       shop: ShopService, settings: Settings) -> None:
    store.clear_awaiting(user.id)
    entered = await _today_entered(callback.message, t, shop, settings)
    if entered is None:
        await callback.answer()
        return
    await callback.message.edit_text(t["shop_menu_prompt"], reply_markup=shop_menu_kb(t, entered))
    await callback.answer()


@router.callback_query(ShopCb.filter(F.action == "noop"))
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(ShopCb.filter(F.action == "cancel"))
async def cancel(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore) -> None:
    store.clear_awaiting(user.id)
    await callback.message.edit_text(t["shop_cancelled"])
    await callback.answer()


@router.callback_query(ShopCb.filter(F.action == "open"))
async def open_today(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
                     shop: ShopService, settings: Settings) -> None:
    """"Bugungi xarajatni kiritish" va soat 10:00 dagi eslatmadagi tugma."""
    await callback.answer()
    day = _today(settings)
    if day.weekday() == 6:
        await callback.message.answer(t["shop_sunday"])
        return
    try:
        found = await shop.get_day(day)
    except Exception:
        logger.exception("Do'kon jadvalini o'qishda xatolik")
        await callback.message.answer(t["error"])
        return
    if found and found[0] and found[1] is not None:       # allaqachon kiritilgan: qo'shish/ayirish
        await _show_adjust_menu(callback.message, t, day, found[1], edit=False)
        return
    await _prompt(callback.message, t, day, shop, user, store, edit=False)


@router.callback_query(ShopCb.filter(F.action == "adjmenu"))
async def adjust_menu(callback: CallbackQuery, t: dict[str, str], user: User, store: UserStore,
                      shop: ShopService, settings: Settings) -> None:
    """"Bugungi do'kon xarajatini o'zgartirish" bosildi: bitta "Qo'shish yoki ayirish" tugmasi."""
    store.clear_awaiting(user.id)
    day = _today(settings)
    if day.weekday() == 6:
        await callback.answer(t["shop_sunday"], show_alert=True)
        return
    try:
        found = await shop.get_day(day)
    except Exception:
        logger.exception("Do'kon jadvalini o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return
    if not found or not found[0] or found[1] is None:
        await callback.answer(t["shop_adj_not_entered"], show_alert=True)
        return
    await callback.answer()
    await _show_adjust_menu(callback.message, t, day, found[1], edit=True)


@router.callback_query(ShopCb.filter(F.action == "adj"))
async def adjust_prompt(callback: CallbackQuery, callback_data: ShopCb, t: dict[str, str],
                        user: User, store: UserStore, shop: ShopService, settings: Settings) -> None:
    """"Qo'shish yoki ayirish" bosildi: ishorali son kutiladi."""
    day = _from_ymd(callback_data.idx)
    if day is None or day != _today(settings):             # faqat BUGUNGI xarajat
        await callback.answer(t["shop_adj_expired"], show_alert=True)
        return
    try:
        found = await shop.get_day(day)
    except Exception:
        logger.exception("Do'kon jadvalini o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return
    if not found or not found[0] or found[1] is None:
        await callback.answer(t["shop_adj_not_entered"], show_alert=True)
        return
    await callback.answer()
    store.set_awaiting(user.id, "shop_adj", _ymd(day))
    await callback.message.edit_text(
        t["shop_adj_prompt"].format(date=day.strftime("%d.%m.%Y"), current=fmt_money(found[1])),
        reply_markup=shop_cancel_kb(t))


# ---------------- istalgan kun: kalendar ----------------
def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + month - 1 + delta
    return idx // 12, idx % 12 + 1


async def _show_calendar(callback: CallbackQuery, t: dict[str, str], lang: str, today: date,
                         year: int, month: int, shop: ShopService) -> None:
    dim = calendar.monthrange(year, month)[1]
    try:
        statuses = await shop.day_statuses([date(year, month, d) for d in range(1, dim + 1)])
    except Exception:
        logger.exception("Kalendar uchun do'kon holatini o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return

    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for d in week:
            if d == 0 or date(year, month, d).weekday() == 6:      # bo'sh katak / yakshanba
                row.append(("·", "noop", 0))
                continue
            dt = date(year, month, d)
            status = statuses[dt]
            if status == "nosheet":
                label = f"🚫{d}"
            elif status == "rec":
                label = f"✅{d}"
            elif dt <= today:
                label = f"⚠️{d}"
            else:
                label = str(d)
            row.append((label, "day", _ymd(dt)))
        weeks.append(row)

    offset = (year * 12 + month) - (today.year * 12 + today.month)
    py, pm = _shift(year, month, -1)
    ny, nm = _shift(year, month, 1)
    await callback.message.edit_text(
        t["shop_cal_title"],
        reply_markup=shop_calendar_kb(
            t, f"{month_name(lang, month)} {year}", [weekday_short(lang, i) for i in range(7)], weeks,
            py * 100 + pm if offset - 1 >= -CAL_MONTHS_BACK else None,
            ny * 100 + nm if offset + 1 <= CAL_MONTHS_FORWARD else None),
    )
    await callback.answer()


@router.callback_query(ShopCb.filter(F.action == "past"))
async def open_calendar(callback: CallbackQuery, t: dict[str, str], lang: str, settings: Settings,
                        shop: ShopService) -> None:
    today = _today(settings)
    await _show_calendar(callback, t, lang, today, today.year, today.month, shop)


@router.callback_query(ShopCb.filter(F.action == "cal"))
async def navigate(callback: CallbackQuery, callback_data: ShopCb, t: dict[str, str], lang: str,
                   settings: Settings, shop: ShopService) -> None:
    year, month = divmod(callback_data.idx, 100)
    today = _today(settings)
    offset = (year * 12 + month) - (today.year * 12 + today.month)
    if not 1 <= month <= 12 or not -CAL_MONTHS_BACK <= offset <= CAL_MONTHS_FORWARD:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await _show_calendar(callback, t, lang, today, year, month, shop)


@router.callback_query(ShopCb.filter(F.action == "day"))
async def choose_day(callback: CallbackQuery, callback_data: ShopCb, t: dict[str, str], user: User,
                     store: UserStore, shop: ShopService, settings: Settings) -> None:
    day = _from_ymd(callback_data.idx)
    if day is None or day.weekday() == 6:
        await callback.answer(t["shop_sunday"] if day else t["no_data"], show_alert=True)
        return
    today = _today(settings)
    if (day.year, day.month) != (today.year, today.month):
        try:
            found = await shop.get_day(day)
        except Exception:
            logger.exception("Do'kon jadvalini o'qishda xatolik")
            await callback.answer(t["error"], show_alert=True)
            return
        if found is None:
            await callback.answer(t["shop_no_block"].format(date=day.strftime("%d.%m.%Y")), show_alert=True)
            return
    await callback.answer()
    await _prompt(callback.message, t, day, shop, user, store, edit=True)


@router.callback_query(ShopCb.filter(F.action == "edit"))
async def edit_again(callback: CallbackQuery, callback_data: ShopCb, t: dict[str, str], user: User,
                     store: UserStore, shop: ShopService) -> None:
    day = _from_ymd(callback_data.idx)
    if day is None or day.weekday() == 6:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await callback.answer()
    await _prompt(callback.message, t, day, shop, user, store, edit=True)


# ---------------- summani qabul qilish ----------------
@router.message(AwaitingAmount())
async def receive_amount(message: Message, t: dict[str, str], user: User, store: UserStore,
                         shop: ShopService, pending: PendingReports, settings: Settings) -> None:
    kind, ymd = store.get_awaiting(user.id)
    if kind == "shop_adj":
        await _receive_adjustment(message, t, user, store, shop, pending, settings, ymd)
        return
    amount = parse_shop_amount(message.text)               # 450 -> 450000, 5 -> 5000
    if amount is None or not 0 <= amount < MAX_AMOUNT:
        await message.answer(t["shop_bad_amount"])           # kutish davom etadi
        return

    day = _from_ymd(ymd)
    today = _today(settings)
    create = (day.year, day.month) == (today.year, today.month)
    try:
        await shop.set_day(day, amount, create)
    except TemplateMissingError as exc:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_no_template"].format(tab=escape(exc.tab)))
        return
    except LayoutError:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_layout_error"])
        return
    except NoBlockError:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_no_block"].format(date=day.strftime("%d.%m.%Y")))
        return
    except Exception:
        logger.exception("Do'kon xarajatini jadvalga yozishda xatolik")
        await message.answer(t["error"])
        return

    store.clear_awaiting(user.id)
    await pending.ready("shop", day.isoformat(), user.id, _now_ts(settings))   # 30 daqiqalik taymer
    month_total = await shop.month_total_until(day)
    await message.answer(
        t["shop_saved"].format(date=day.strftime("%d.%m.%Y"), amount=fmt_money(amount),
                               month_total=fmt_money(month_total)),
        reply_markup=shop_saved_kb(t, _ymd(day)),
    )


async def _receive_adjustment(message: Message, t: dict[str, str], user: User, store: UserStore,
                              shop: ShopService, pending: PendingReports, settings: Settings,
                              ymd: int) -> None:
    """Bugungi xarajatga ishorali son: musbat -> qo'shiladi, manfiy -> ayiriladi."""
    delta = parse_shop_amount(message.text, signed=True)
    if delta is None or abs(delta) >= MAX_AMOUNT:
        await message.answer(t["shop_adj_bad"])             # kutish davom etadi
        return
    if delta == 0:
        await message.answer(t["shop_adj_zero"])
        return

    day = _from_ymd(ymd)
    if day is None or day != _today(settings):              # yarim tunda sana o'tib ketgan bo'lishi mumkin
        store.clear_awaiting(user.id)
        await message.answer(t["shop_adj_expired"])
        return
    try:
        old, new = await shop.adjust_day(day, delta)
    except NegativeResultError as exc:
        await message.answer(t["shop_adj_negative"].format(current=fmt_money(exc.current)))
        return                                              # kutish davom etadi
    except NotEnteredError:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_adj_not_entered"])
        return
    except TemplateMissingError as exc:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_no_template"].format(tab=escape(exc.tab)))
        return
    except LayoutError:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_layout_error"])
        return
    except NoBlockError:
        store.clear_awaiting(user.id)
        await message.answer(t["shop_no_block"].format(date=day.strftime("%d.%m.%Y")))
        return
    except Exception:
        logger.exception("Do'kon xarajatini qo'shish/ayirishda xatolik")
        await message.answer(t["error"])
        return

    store.clear_awaiting(user.id)
    await pending.ready("shop", day.isoformat(), user.id, _now_ts(settings))   # taymer qayta boshlanadi
    month_total = await shop.month_total_until(day)
    change = ("+" if delta > 0 else "-") + fmt_money(abs(delta))
    await message.answer(
        t["shop_adj_saved"].format(date=day.strftime("%d.%m.%Y"), old=fmt_money(old), change=change,
                                   new=fmt_money(new), month_total=fmt_money(month_total)),
        reply_markup=shop_saved_kb(t, _ymd(day)),
    )


# ---------------- bossga yuborish ----------------
@router.callback_query(ShopCb.filter(F.action == "send"))
async def send_to_boss(callback: CallbackQuery, callback_data: ShopCb, t: dict[str, str],
                       shop: ShopService, repo: UserRepository, bot: Bot, pending: PendingReports,
                       settings: Settings) -> None:
    day = _from_ymd(callback_data.idx)
    if day is None:
        await callback.answer(t["no_data"], show_alert=True)
        return
    key = day.isoformat()
    if await pending.is_sent("shop", key) is True:
        await callback.answer(t["shop_already_sent"], show_alert=True)
        return
    try:
        found = await shop.get_day(day)
    except Exception:
        logger.exception("Do'kon jadvalini o'qishda xatolik")
        await callback.answer(t["error"], show_alert=True)
        return
    if not found or not found[0] or found[1] is None:
        await callback.answer(t["no_data"], show_alert=True)
        return

    amount = found[1]
    month_total = await shop.month_total_until(day)
    delivered = await send_to_bosses(bot, repo, lambda bt, lang: format_shop_daily(bt, day, amount, month_total))
    if delivered == 0:
        await callback.answer(t["att_no_boss"], show_alert=True)
        return
    await pending.mark_sent("shop", key, _now_ts(settings))
    await callback.message.edit_text(
        t["shop_sent"].format(date=day.strftime("%d.%m.%Y"), count=delivered),
        reply_markup=shop_edit_kb(t, _ymd(day)),
    )
    await callback.answer()
