import logging
import re
from html import escape
from io import BytesIO
from typing import List, Optional

from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.permissions import Role
from app.keyboards import warehouse as wkb
from app.keyboards.reply import main_menu_kb
from app.handlers.ai_confirm import confirm_kb
from app.models import User
from app.services.ai_agent import AgentBusy, AgentCooldown, AgentDenied, ai_allowed, is_trivial
from app.services.categories import label as cat_label
from app.services.search import exact_match, normalize, search_products

logger = logging.getLogger(__name__)
router = Router(name="fallback")

LOOKUP_ROLES = (Role.STOREKEEPER, Role.ADMIN, Role.BOSS)
ACTION_ROLES = (Role.STOREKEEPER, Role.ADMIN)          # 📥 / 📤 / ✏️ tugmalari faqat shularga
MAX_SHOWN = 5
MAX_MEDIA = 15 * 1024 * 1024                          # Gemini ga inline yuboriladigan fayl chegarasi
_QUESTION_WORDS = {"qancha", "nechta", "qaysi", "qanday", "nima", "nimaga", "qachon", "kim", "qayerda", "nega",
                   "сколько", "какие", "какой", "какая", "как", "почему", "когда", "что", "кто", "где"}


def _n(x: float) -> str:
    s = f"{round(float(x or 0), 2):,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


@router.callback_query()
async def denied(callback: CallbackQuery, t: dict[str, str]) -> None:
    """Hech bir handler olmagan (odatda ruxsat yetmagan) tugma bosilsa."""
    await callback.answer(t["no_permission"], show_alert=True)


def _looks_like_question(text: str) -> bool:
    """Savol yoki gap (mahsulot nomi emas): AI yordamchiga."""
    words = re.findall(r"\w+", text.casefold())
    return "?" in text or len(words) >= 5 or any(w in _QUESTION_WORDS for w in words)


async def _lookup(message: Message, t: dict[str, str], user: User, state: FSMContext, warehouse,
                  ai_on: bool = False) -> str:
    """🔎 Erkin matn: «Остатки и Расходы» dan mahsulot qidiriladi (aniq, qism-so'z, o'xshash).
    Topilsa — qoldiq, birlik, Мин. норматив, o'rtacha narx; sklad/admin uchun 📥 / 📤 / ✏️ / 💰 / ⚖️ tugmalari.
    Natija: "shown" — javob berildi; "ai" — AI yordamchiga berish kerak; "" — tushunilmadi.
    ai_on=True: o'xshash (taxminiy) natija va «topilmadi» o'rniga AI javob beradi."""
    query = " ".join((message.text or "").split())[:60]
    if len(query.replace(" ", "")) < 2 or query.startswith("/"):
        return "ai" if ai_on and query and not query.startswith("/") else ""
    try:
        catalog = await warehouse.catalog(force=True)     # jadvalda qo'lda qilingan o'zgarish darhol
    except Exception:
        logger.exception("Erkin qidiruv: katalogni o'qib bo'lmadi")
        return "ai" if ai_on else ""
    names = [p.name for p in catalog]
    exact = exact_match(query, names)
    if exact:
        hits: List[str] = [exact]
    else:
        q = normalize(query).replace(" ", "")
        hits = sorted((n for n in names if q and q in normalize(n).replace(" ", "")),
                      key=lambda n: (len(n), n.casefold()))[:MAX_SHOWN]
        if not hits and ai_on:
            return "ai"                                 # xato yozilgan nom ham — AI search_products bilan topadi
        hits = hits or search_products(query, names, limit=3)
    actions = user.role in ACTION_ROLES
    if not hits:
        if actions:
            await state.clear()
            await state.update_data(lookup_q=query)
            await message.answer(t["wh_lookup_nf"].format(text=escape(query)), reply_markup=wkb.lookup_add_kb(t, query))
        else:
            await message.answer(t["wh_lookup_nf_boss"].format(text=escape(query)))
        return "shown"
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
        return "shown"
    await state.clear()
    await state.update_data(lookup=hits)
    if len(cards) == 1:
        await message.answer(head + "\n\n" + cards[0], reply_markup=wkb.lookup_kb(t, 0))
        return "shown"
    # bir nechta mahsulot: har bir karta alohida xabar — ixcham [📥] [📤] [✏️] [💰] [⚖️] qaysi tovarga tegishli aniq
    await message.answer(head)
    for i, card in enumerate(cards):
        await message.answer(card, reply_markup=wkb.lookup_kb(t, i))
    return "shown"


async def _download(message: Message) -> Optional[bytes]:
    media = message.voice or (message.photo[-1] if message.photo else None)
    if media is None or (getattr(media, "file_size", 0) or 0) > MAX_MEDIA:
        return None
    buf = BytesIO()
    await message.bot.download(media, destination=buf)
    return buf.getvalue()


async def _ask_ai(message: Message, t: dict[str, str], user: User, state: Optional[FSMContext], ai_agent) -> None:
    """🤖 AI yordamchi: matn / ovoz / rasm. Javob kelguncha «yozmoqda…» ko'rsatiladi."""
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:
        pass
    audio = image = None
    try:
        if message.voice or message.photo:
            data = await _download(message)
            if data is None:
                await message.answer(t["ai_media_big"])
                return
            if message.voice:
                audio = data
            else:
                image = data
        reply = await ai_agent.ask(user.id, user.role, t.get("lang_code", "uz"),
                                   text=message.text or message.caption or "", audio=audio, image=image,
                                   name=user.full_name)
    except AgentDenied:
        await message.answer(t["unknown_command"], reply_markup=main_menu_kb(t, user.role, user.real_role))
        return
    except AgentCooldown:
        await message.answer(t["ai_wait"])
        return
    except AgentBusy:
        await message.answer(t["ai_busy"])
        return
    except Exception:
        logger.exception("AI yordamchi xatosi")
        await message.answer(t["ai_error"])
        return
    markup = None
    query = " ".join((message.text or "").split())[:60]
    if (user.role in ACTION_ROLES and query and not _looks_like_question(query) and len(query.split()) <= 4
            and state is not None and not reply.drafts):
        # nom kabi qisqa matn katalogda topilmadi: AI javobi + «➕ ... ni omborga qo'shish»
        await state.update_data(lookup_q=query)
        markup = wkb.lookup_add_kb(t, query)
    await message.answer(reply.text, reply_markup=markup)
    for draft in reply.drafts:                     # ✍️ yozish faqat tasdiqlangandan keyin
        await message.answer(draft.card, reply_markup=confirm_kb(t, draft.id))


@router.message()
async def unknown_message(message: Message, t: dict[str, str], user: User | None = None,
                          state: Optional[FSMContext] = None, warehouse=None, ai_agent=None) -> None:
    """Hech bir handler mos kelmasa.

    Faol jarayon (FSM holati) bo'lmasa:
    - mahsulot nomi (aniq / qism-so'z) — sklad / admin / boss uchun darhol mahsulot kartasi;
    - savol, gap, xato yozilgan nom, ovozli xabar, rasm — 🤖 AI yordamchi (GEMINI_API_KEY bo'lsa).
    Qolganlarga: tushuntirib, menyuni hozirgi rolga moslab yangilaymiz."""
    if user is None:
        return
    in_flow = state is not None and await state.get_state() is not None
    # 🤖 faqat Boss / Admin / Sklad (faol rol bo'yicha); ishchiga Gemini umuman chaqirilmaydi
    ai_on = bool(ai_agent is not None and getattr(ai_agent, "enabled", False) and not in_flow
                 and ai_allowed(user.role))
    if ai_on and (message.voice or message.photo):
        await _ask_ai(message, t, user, state, ai_agent)
        return
    if ai_on and message.text and is_trivial(message.text):
        ai_on = False                              # «+», «.», «ok», emoji — API chaqirilmaydi
    if message.text and not message.text.startswith("/"):
        if ai_on and _looks_like_question(message.text):
            await _ask_ai(message, t, user, state, ai_agent)
            return
        if user.role in LOOKUP_ROLES and warehouse is not None and state is not None:
            res = await _lookup(message, t, user, state, warehouse, ai_on=ai_on)
            if res == "shown":
                return
            if res == "ai":
                await _ask_ai(message, t, user, state, ai_agent)
                return
    logger.info("Handler topilmadi: user_id=%s role=%s text=%r",
                user.id, user.role.value if user.role else None, message.text)
    await message.answer(t["unknown_command"], reply_markup=main_menu_kb(t, user.role, user.real_role))
