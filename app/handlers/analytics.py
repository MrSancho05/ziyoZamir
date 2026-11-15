"""Boss va Admin uchun tahlil: "Davomatni tekshirish" va "Do'kon xarajatlarini tekshirish".

Oqim: davr tanlanadi (kunlik / haftalik / oylik / mavsumiy / yillik) -> qisqacha xulosa xabari
+ "Batafsil" tugmasi. "Batafsil" va "Qisqacha" bosilganda YANGI XABAR YUBORILMAYDI: shu xabarning o'zi
`edit_text` bilan almashtiriladi.
"""
import logging
import time
from datetime import date, datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission, has_permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import InspectCb, ViewCb
from app.keyboards.inline import analytics_menu_kb, analytics_result_kb, analytics_season_kb
from app.locales.texts import all_texts, weekday_name
from app.models import User
from app.services.attendance import AttendanceService
from app.services.attendance_grid import is_markable_day
from app.services.inspect_reports import Rendered, attendance_rendered, shop_rendered
from app.services.shop import ShopService
from app.storage.memory import UserStore
from app.utils.periods import TF_DAILY, TF_MONTHLY, TF_SEASONAL, TIMEFRAMES, build_range

logger = logging.getLogger(__name__)
router = Router(name="analytics")

KIND_ATT, KIND_SHOP = "att", "shop"
PERMISSIONS = {KIND_ATT: Permission.VIEW_ATTENDANCE, KIND_SHOP: Permission.VIEW_EXPENSES}

# "Batafsil" <-> "Qisqacha" almashtirishda Google Sheets qayta o'qilmasligi uchun qisqa muddatli kesh.
# Kalit tilni ham o'z ichiga oladi (matnlar tilga bog'liq). Muddati o'tsa, ma'lumot qaytadan o'qiladi.
CACHE_TTL = 300
CACHE_MAX = 256
_cache: dict[tuple, tuple[float, Rendered]] = {}


def _cache_get(key: tuple) -> Rendered | None:
    item = _cache.get(key)
    return item[1] if item and time.monotonic() - item[0] < CACHE_TTL else None


def _cache_put(key: tuple, value: Rendered) -> None:
    now = time.monotonic()
    if len(_cache) >= CACHE_MAX:
        for k in [k for k, (ts, _) in _cache.items() if now - ts >= CACHE_TTL]:
            del _cache[k]
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
    _cache[key] = (now, value)


def _ymd(d: date) -> int:
    return int(d.strftime("%Y%m%d"))


def _from_ymd(ymd: int) -> date | None:
    try:
        return date(ymd // 10000, ymd // 100 % 100, ymd % 100)
    except ValueError:
        return None


def _allowed(user: User, kind: str) -> bool:
    perm = PERMISSIONS.get(kind)
    return perm is not None and has_permission(user.role, perm)


def _menu_text(t: dict[str, str], kind: str) -> str:
    return t["an_shop_prompt"] if kind == KIND_SHOP else t["view_prompt"]


async def _render(kind: str, cb: InspectCb, today: date, t: dict[str, str], lang: str,
                  shop: ShopService, attendance: AttendanceService) -> Rendered:
    """Tanlangan davr uchun matnlarni tayyorlaydi (keshdan yoki Google Sheets dan)."""
    key = (kind, lang, cb.tf, cb.season, _ymd(today))
    cached = _cache_get(key)
    if cached is not None:
        return cached

    rng = build_range(cb.tf, today, cb.season)
    if kind == KIND_SHOP:
        if rng.tf == TF_DAILY and rng.start.weekday() == 6:
            return Rendered(empty=t["shop_sunday"])                 # yakshanba: xarajat kiritilmaydi
        cur = await shop.period_stats(rng.start, rng.end)
        prev = await shop.period_stats(rng.prev_start, rng.prev_end)
        rendered = shop_rendered(t, lang, rng, cur, prev)
    else:
        if rng.tf == TF_DAILY and not is_markable_day(rng.start):
            return Rendered(empty=t["att_weekend"].format(weekday=weekday_name(lang, rng.start.weekday())))
        report = None
        if rng.tf == TF_MONTHLY:                                    # oylik: maosh hisobi bilan
            report = await attendance.month_payroll(rng.end.year, rng.end.month, rng.end)
            stats = report.stats
        else:
            stats = await attendance.period_stats(rng.start, rng.end)
        rendered = attendance_rendered(t, lang, rng, stats, report)

    if rendered.empty is None:
        _cache_put(key, rendered)           # bo'sh natija keshlanmaydi: ma'lumot kiritilsa darrov ko'rinadi
    return rendered


async def _safe_edit(message: Message, text: str, reply_markup) -> None:
    """Matn o'zgarmagan bo'lsa (tugma ikki marta bosilgan) Telegram xato beradi: buni jim o'tkazamiz."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


# ---------------- menyu tugmalari (pastki klaviatura) ----------------
@router.message(F.text.in_(all_texts("btn_check")), HasPermission(Permission.VIEW_ATTENDANCE))
async def open_attendance_menu(message: Message, t: dict[str, str], user: User, store: UserStore) -> None:
    store.clear_awaiting(user.id)
    await message.answer(_menu_text(t, KIND_ATT), reply_markup=analytics_menu_kb(t, KIND_ATT))


@router.message(F.text.in_(all_texts("btn_shop_check")), HasPermission(Permission.VIEW_EXPENSES))
async def open_expenses_menu(message: Message, t: dict[str, str], user: User, store: UserStore) -> None:
    store.clear_awaiting(user.id)
    await message.answer(_menu_text(t, KIND_SHOP), reply_markup=analytics_menu_kb(t, KIND_SHOP))


# Eski ("Bugun / O'tgan hafta / Shu oy") tugmalari Telegramda qolib ketgan bo'lsa: yangi menyuga o'tkazamiz
@router.callback_query(ViewCb.filter(), HasPermission(Permission.VIEW_ATTENDANCE))
async def legacy_view_menu(callback: CallbackQuery, t: dict[str, str]) -> None:
    await _safe_edit(callback.message, _menu_text(t, KIND_ATT), analytics_menu_kb(t, KIND_ATT))
    await callback.answer()


# ---------------- davr tanlash ----------------
@router.callback_query(InspectCb.filter(F.action == "menu"))
async def back_to_menu(callback: CallbackQuery, callback_data: InspectCb, t: dict[str, str], user: User) -> None:
    kind = callback_data.kind
    if not _allowed(user, kind):
        await callback.answer(t["no_permission"], show_alert=True)
        return
    await _safe_edit(callback.message, _menu_text(t, kind), analytics_menu_kb(t, kind))
    await callback.answer()


@router.callback_query(InspectCb.filter(F.action == "tf"))
async def choose_timeframe(callback: CallbackQuery, callback_data: InspectCb, t: dict[str, str], lang: str,
                           user: User, settings: Settings, shop: ShopService,
                           attendance: AttendanceService) -> None:
    kind = callback_data.kind
    if not _allowed(user, kind):
        await callback.answer(t["no_permission"], show_alert=True)
        return
    if callback_data.tf == TF_SEASONAL:                             # mavsumiy: avval mavsumni tanlaydi
        await _safe_edit(callback.message, t["an_season_prompt"], analytics_season_kb(t, kind))
        await callback.answer()
        return
    await _send_result(callback, callback_data, t, lang, settings, shop, attendance)


@router.callback_query(InspectCb.filter(F.action == "go"))
async def choose_season(callback: CallbackQuery, callback_data: InspectCb, t: dict[str, str], lang: str,
                        user: User, settings: Settings, shop: ShopService,
                        attendance: AttendanceService) -> None:
    if not _allowed(user, callback_data.kind):
        await callback.answer(t["no_permission"], show_alert=True)
        return
    await _send_result(callback, callback_data, t, lang, settings, shop, attendance)


async def _send_result(callback: CallbackQuery, cb: InspectCb, t: dict[str, str], lang: str, settings: Settings,
                       shop: ShopService, attendance: AttendanceService) -> None:
    """Natijaning qisqacha ko'rinishini yuboradi (bu YANGI xabar: menyu o'z joyida qoladi)."""
    if cb.tf not in TIMEFRAMES:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await callback.answer()                                         # Sheets sekin bo'lsa, tugma "osilib" qolmasin
    today = datetime.now(settings.timezone).date()
    try:
        rendered = await _render(cb.kind, cb, today, t, lang, shop, attendance)
    except Exception:
        logger.exception("Tahlilni tayyorlashda xatolik: %s %s", cb.kind, cb.tf)
        await callback.message.answer(t["error"])
        return
    if rendered.empty is not None:                                  # ma'lumot yo'q / yetarli emas
        await callback.message.answer(rendered.empty)
        return
    await callback.message.answer(
        rendered.brief,
        reply_markup=analytics_result_kb(t, cb.kind, cb.tf, cb.season, _ymd(today), showing="b"))


# ---------------- "Batafsil" <-> "Qisqacha": xabar o'sha joyida tahrirlanadi ----------------
@router.callback_query(InspectCb.filter(F.action == "tog"))
async def toggle_detail(callback: CallbackQuery, callback_data: InspectCb, t: dict[str, str], lang: str,
                        user: User, shop: ShopService, attendance: AttendanceService) -> None:
    cb = callback_data
    if not _allowed(user, cb.kind):
        await callback.answer(t["no_permission"], show_alert=True)
        return
    today = _from_ymd(cb.ymd)
    if today is None or cb.tf not in TIMEFRAMES:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await callback.answer()
    try:
        rendered = await _render(cb.kind, cb, today, t, lang, shop, attendance)
    except Exception:
        logger.exception("Tahlilni qayta tayyorlashda xatolik: %s %s", cb.kind, cb.tf)
        await callback.message.answer(t["error"])
        return
    if rendered.empty is not None:                                  # ma'lumot o'chib ketgan bo'lsa
        await _safe_edit(callback.message, rendered.empty, None)
        return
    detailed = cb.mode == "d"
    await _safe_edit(
        callback.message,
        rendered.detail if detailed else rendered.brief,
        analytics_result_kb(t, cb.kind, cb.tf, cb.season, cb.ymd, showing="d" if detailed else "b"))
