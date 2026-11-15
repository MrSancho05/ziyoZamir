"""Top mahsulotlar (FAQAT admin): kirim (Приход) va chiqim (Расход) bo'yicha ombor aylanuvchanligi reytingi
va saqlash joyi bo'yicha tavsiyalar. Natija yangi xabar(lar) bilan yuboriladi, davr menyusi joyida qoladi."""
import logging
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.core.permissions import Permission
from app.filters.permission import HasPermission
from app.keyboards.callbacks import TopCb
from app.keyboards.inline import top_periods_kb
from app.locales.texts import all_texts
from app.models import User
from app.services.flows import FlowsService
from app.services.top_products import build_report
from app.services.top_products_text import render_top
from app.storage.memory import UserStore
from app.utils.text import split_message

logger = logging.getLogger(__name__)
router = Router(name="top_products")

router.message.filter(HasPermission(Permission.VIEW_TOP_PRODUCTS))
router.callback_query.filter(HasPermission(Permission.VIEW_TOP_PRODUCTS))

PERIOD_DAYS: dict[str, int | None] = {"30": 30, "90": 90, "all": None}
_PERIOD_LABEL = {"30": "top_period_30", "90": "top_period_90", "all": "top_period_all"}

# Google Sheets ni har bosishda qayta o'qimaslik uchun qisqa muddatli kesh (til va kun bo'yicha)
CACHE_TTL = 300
_cache: dict[tuple, tuple[float, str | None]] = {}


@router.message(F.text.in_(all_texts("btn_top_products")))
async def open_menu(message: Message, t: dict[str, str], user: User, store: UserStore) -> None:
    store.clear_awaiting(user.id)
    await message.answer(t["top_prompt"], reply_markup=top_periods_kb(t))


@router.callback_query(TopCb.filter())
async def show_top(callback: CallbackQuery, callback_data: TopCb, t: dict[str, str], lang: str,
                   settings: Settings, flows: FlowsService) -> None:
    period = callback_data.period
    if period not in PERIOD_DAYS:
        await callback.answer(t["no_data"], show_alert=True)
        return
    await callback.answer()                      # Sheets sekin bo'lsa, tugma "osilib" qolmasin
    today = datetime.now(settings.timezone).date()
    key = (lang, period, today.toordinal())
    cached = _cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < CACHE_TTL:
        text = cached[1]
    else:
        try:
            tables = await flows.tables()
        except Exception:
            logger.exception("Top mahsulotlar: Приход/Расход varaqlarini o'qishda xatolik")
            await callback.message.answer(t["error"])
            return
        report = build_report(tables["inflow"], tables["outflow"], today, PERIOD_DAYS[period])
        if not report.in_found and not report.out_found:
            text = t["top_no_sheets"].format(
                tabs=f"{flows.tabs['inflow']}, {flows.tabs['outflow']}")
        elif report.empty:
            text = t["top_no_data"].format(period=t[_PERIOD_LABEL[period]])
        else:
            text = render_top(t, report, t[_PERIOD_LABEL[period]], flows.tabs)
        if len(_cache) >= 64:
            _cache.clear()
        if report.items:                          # bo'sh natija keshlanmaydi: ma'lumot kiritilsa darrov ko'rinadi
            _cache[key] = (time.monotonic(), text)
    for part in split_message(text):
        await callback.message.answer(part)
