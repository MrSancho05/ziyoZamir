import logging
from html import escape
from typing import List, Optional

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.permissions import Role
from app.keyboards import warehouse as wkb
from app.keyboards.reply import main_menu_kb
from app.models import User
from app.services.categories import label as cat_label
from app.services.search import exact_match, normalize, search_products

logger = logging.getLogger(__name__)
router = Router(name="fallback")

LOOKUP_ROLES = (Role.STOREKEEPER, Role.ADMIN, Role.BOSS)
ACTION_ROLES = (Role.STOREKEEPER, Role.ADMIN)          # 📥 / 📤 / ✏️ tugmalari faqat shularga
MAX_SHOWN = 5


def _n(x: float) -> str:
    s = f"{round(float(x or 0), 2):,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


@router.callback_query()
async def denied(callback: CallbackQuery, t: dict[str, str]) -> None:
    """Hech bir handler olmagan (odatda ruxsat yetmagan) tugma bosilsa."""
    await callback.answer(t["no_permission"], show_alert=True)


async def _lookup(message: Message, t: dict[str, str], user: User, state: FSMContext, warehouse) -> bool:
    """🔎 Erkin matn: «Остатки и Расходы» dan mahsulot qidiriladi (aniq, qism-so'z, o'xshash).
    Topilsa — qoldiq, birlik, Мин. норматив, o'rtacha narx; sklad/admin uchun 📥 / 📤 / ✏️ tugmalari.
    Topilmasa — sklad/admin: «➕ ... ni omborga qo'shish»; boss: oddiy javob. True — javob berildi."""
    query = " ".join((message.text or "").split())[:60]
    if len(query.replace(" ", "")) < 2 or query.startswith("/"):
        return False
    try:
        catalog = await warehouse.catalog(force=True)     # jadvalda qo'lda qilingan o'zgarish darhol
    except Exception:
        logger.exception("Erkin qidiruv: katalogni o'qib bo'lmadi")
        return False
    names = [p.name for p in catalog]
    exact = exact_match(query, names)
    if exact:
        hits: List[str] = [exact]
    else:
        q = normalize(query).replace(" ", "")
        hits = sorted((n for n in names if q and q in normalize(n).replace(" ", "")),
                      key=lambda n: (len(n), n.casefold()))[:MAX_SHOWN] or search_products(query, names, limit=3)
    actions = user.role in ACTION_ROLES
    if not hits:
        if actions:
            await state.clear()
            await state.update_data(lookup_q=query)
            await message.answer(t["wh_lookup_nf"].format(text=escape(query)), reply_markup=wkb.lookup_add_kb(t, query))
        else:
            await message.answer(t["wh_lookup_nf_boss"].format(text=escape(query)))
        return True
    by_name = {p.name: p for p in catalog}
    lang = t.get("lang_code", "uz")
    cards = []
    for name in hits:
        p = by_name[name]
        low = " ⚠️" if p.is_low else ""
        cards.append(t["wh_lookup_card"].format(
            name=escape(p.name), cat=escape(cat_label(p.category, lang) if p.category else "—"),
            stock=_n(p.stock), unit=escape(p.unit), min=_n(p.min_stock), price=_n(p.avg_price), low=low))
    head = t["wh_lookup_found"].format(text=escape(query), n=len(hits))
    if not actions:                                    # boss: bitta xabar, tugmasiz
        await message.answer(head + "\n\n" + "\n\n".join(cards))
        return True
    await state.clear()
    await state.update_data(lookup=hits)
    if len(cards) == 1:
        await message.answer(head + "\n\n" + cards[0], reply_markup=wkb.lookup_kb(t, 0))
        return True
    # bir nechta mahsulot: har bir karta alohida xabar — ixcham [📥] [📤] [✏️] [💰] qaysi tovarga tegishli aniq
    await message.answer(head)
    for i, card in enumerate(cards):
        await message.answer(card, reply_markup=wkb.lookup_kb(t, i))
    return True


@router.message()
async def unknown_message(message: Message, t: dict[str, str], user: User | None = None,
                          state: Optional[FSMContext] = None, warehouse=None) -> None:
    """Hech bir handler mos kelmasa.

    Sklad nazoratchisi / Admin / Boss yozgan har qanday matn — mahsulot qidiruvi (aks holda "Buyruq tushunilmadi"
    o'rniga foydali javob). Qolganlarga: tushuntirib, menyuni hozirgi rolga moslab yangilaymiz."""
    if user is None:
        return
    if (user.role in LOOKUP_ROLES and message.text and warehouse is not None and state is not None
            and await _lookup(message, t, user, state, warehouse)):
        return
    logger.info("Handler topilmadi: user_id=%s role=%s text=%r",
                user.id, user.role.value if user.role else None, message.text)
    await message.answer(t["unknown_command"], reply_markup=main_menu_kb(t, user.role, user.real_role))
