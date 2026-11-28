"""🤖 AI yordamchi (ovoz va matn): Google Gemini + botning o'z funksiyalari (tool calling).

Modellar: asosiy (standart `gemini-3.5-flash`) va zaxira (standart `gemini-3.5-flash-lite`). Asosiy model 429 /
503 / 404 qaytarsa — xuddi shu so'rov (suhbat tarixi va vositalar bilan) zaxira modelda darhol qayta yuboriladi.
«Band» xabari faqat IKKALA model ham 429 qaytarsa.

Kim foydalanadi: Boss, Admin, Sklad nazoratchisi va Usta (faol rol bo'yicha, 🎭 Rolni sinash ham). Ishchi —
Gemini umuman chaqirilmaydi.
- Usta: qoldiq va tanqislar (narxsiz), 📤 Chiqim qoralamasi.
- Boss: faqat o'qish va tahlil.
- Admin / Sklad: o'qish + barcha yozish / tahrirlash / o'chirish — FAQAT qoralama orqali: foydalanuvchi
  «✅ Tasdiqlash» ni bosgandan keyin `app/handlers/ai_confirm.py` botning yozish funksiyalarini chaqiradi.
- Til almashtirish (`change_language`) — tasdiqlashsiz, darhol.
Bir xabarda bir nechta amal (zanjir): 6 tagacha vosita bosqichi. Faqat zavod ishi (boshqa mavzular — 1 jumla
bilan rad). Tejash: max_output_tokens=450, 4 ta xabarlik xotira (10 daqiqa), 1–2 belgili xabarlarga API yo'q.
Python 3.9+ bilan mos (google-genai>=1.0.0)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from app.core.permissions import Permission, Role, has_permission
from app.locales.texts import get_texts
from app.services import kromka
from app.services.categories import label as cat_label
from app.services.categories import suggest as suggest_category
from app.services.drafts import DRAFT_TTL, DRAFTS, Draft, DraftStore  # noqa: F401  (Draft — tashqi importlar uchun)
from app.services.search import exact_match, normalize, search_products
from app.services.shop_grid import parse_shop_amount

logger = logging.getLogger(__name__)

MAX_ROUNDS = 6               # bitta xabarda zanjir vosita bosqichlari
COOLDOWN = 3.0               # soniya: bitta foydalanuvchi so'rovlari orasida
HISTORY_TURNS = 4            # suhbat xotirasi: oxirgi 4 ta xabar (2 savol + 2 javob)
HISTORY_TTL = 600.0          # 10 daqiqa jim turilsa xotira o'chadi
MAX_DRAFTS_PER_ASK = 6
MAX_OUTPUT_TOKENS = 450
TEMPERATURE = 0.2            # faqat 1.x / 2.x modellarga: Gemini 3+ da temperature eskirgan (e'tiborsiz)
PRIMARY_SKIP = 60.0          # asosiy model 429 bersa — shuncha soniya to'g'ridan-to'g'ri zaxiraga
FALLBACK_CODES = (429, 503, 404)
MAX_ANSWER = 3800
MAX_RANGE_DAYS = 366

AI_ROLES = (Role.ADMIN, Role.BOSS, Role.STOREKEEPER, Role.MASTER)
EXEC_ROLES = (Role.ADMIN, Role.BOSS, Role.STOREKEEPER)            # pul, narx, tahlil
WRITE_ROLES = (Role.ADMIN, Role.STOREKEEPER)
OUTFLOW_ROLES = WRITE_ROLES + (Role.MASTER,)
PRICE_ROLES = EXEC_ROLES


class AgentBusy(Exception):
    """Ikkala model ham limitda (429)."""


class AgentCooldown(Exception):
    pass


class AgentDenied(Exception):
    """Bu rol uchun AI yordamchi yopiq."""


def ai_allowed(role: Optional[Role]) -> bool:
    return role in AI_ROLES


def is_trivial(text: str) -> bool:
    """«+», «-», «.», «?», «ok», faqat emoji — Gemini chaqirilmaydi."""
    s = re.sub(r"\s+", "", str(text or ""))
    return len(s) <= 2 or not re.search(r"\w", s)


def _r(x: Any) -> Any:
    try:
        v = round(float(x or 0), 2)
    except (TypeError, ValueError):
        return x
    return int(v) if v == int(v) else v


def _n(x: Any) -> str:
    try:
        s = f"{round(float(x or 0), 2):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(x)
    return s.replace(",", " ")


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".").replace("−", "-")
    try:
        return float(re.sub(r"[^\d.\-+]", "", s))
    except ValueError:
        return None


def _money(value: Any) -> Optional[int]:
    """Do'kon / boshqa xarajat summasi: «450» -> 450 000 (botdagi qisqa yozuv bilan bir xil)."""
    v = _num(value)
    if v is None:
        return None
    raw = str(int(v)) if float(v).is_integer() else str(v)
    return parse_shop_amount(raw)


def _parse_day(value: Any, default: Optional[date]) -> Optional[date]:
    s = str(value or "").strip()
    if not s:
        return default
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return default


def _clean(value: Any, limit: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip().lstrip("=+@").strip()
    return s[:limit]


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def to_telegram_html(text: str) -> str:
    """Modelning markdown javobi -> xavfsiz Telegram HTML (faqat <b>, <i>, <code>)."""
    s = html.escape(str(text or "").strip(), quote=False)
    s = re.sub(r"^\s{0,3}#{1,6}\s*(.+)$", r"<b>\1</b>", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"^(\s*)[*\-]\s+", r"\1• ", s, flags=re.M)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = s.replace("```", "")
    if len(s) > MAX_ANSWER:
        s = s[:MAX_ANSWER].rsplit("\n", 1)[0] + "\n…"
    return s


# ---------------- vositalar ----------------
@dataclass
class Ctx:
    user_id: int
    role: Optional[Role]
    lang: str
    name: str = ""
    drafts: List[Draft] = field(default_factory=list)
    new_lang: Optional[str] = None


@dataclass
class Tool:
    name: str
    description: str
    params: Dict[str, Dict[str, str]]          # nom -> {"type": "string"|"number", "description": ...}
    required: Sequence[str]
    allow: Callable[[Optional[Role]], bool]
    run: Callable[..., Awaitable[Any]]
    write: bool = False


def _perm(*perms: Permission) -> Callable[[Optional[Role]], bool]:
    return lambda role: ai_allowed(role) and any(has_permission(role, p) for p in perms)


def _roles(*roles: Role) -> Callable[[Optional[Role]], bool]:
    return lambda role: role in roles


def _writer(perm: Permission) -> Callable[[Optional[Role]], bool]:
    return lambda role: role in WRITE_ROLES and has_permission(role, perm)


@dataclass
class AgentReply:
    text: str
    drafts: List[Draft] = field(default_factory=list)
    new_lang: Optional[str] = None


class AIAgent:
    def __init__(self, api_key: str, model: str, tz, warehouse=None, flows=None, shop=None, attendance=None,
                 pending=None, client: Any = None, fallback_model: Optional[str] = None) -> None:
        self.model = model                                  # asosiy model
        self.fallback_model = fallback_model if fallback_model and fallback_model != model else None
        self._tz = tz
        self._wh, self._flows, self._shop, self._att, self._pending = warehouse, flows, shop, attendance, pending
        self._client = client
        if self._client is None and api_key:
            from google import genai                       # faqat kalit bo'lsa yuklanadi
            self._client = genai.Client(api_key=api_key)
        self._sem = asyncio.Semaphore(2)
        self._last: Dict[int, float] = {}
        self._history: Dict[int, Tuple[float, Deque[Any]]] = {}
        self._primary_down_until = 0.0
        self.drafts = DRAFTS
        self._tools: Dict[str, Tool] = {t.name: t for t in self._build_tools()}

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def today(self) -> date:
        return datetime.now(self._tz).date()

    def forget(self, user_id: int) -> None:
        self._history.pop(user_id, None)

    def _build_tools(self) -> List[Tool]:
        S, N = "string", "number"
        period = {"start_date": {"type": S, "description": "Boshlanish sanasi, YYYY-MM-DD"},
                  "end_date": {"type": S, "description": "Tugash sanasi, YYYY-MM-DD (kiritilmasa — bugun)"}}
        product = {"type": S, "description": "Mahsulot nomi (taxminiy, kirill/lotin bo'lishi mumkin)"}
        day = {"type": S, "description": "Sana YYYY-MM-DD (kiritilmasa — bugun)"}
        kind = {"type": S, "description": "inflow (Приход) | outflow (Расход) | other (Прочие расходы) | shop (Магазин)"}
        read_stock = _perm(Permission.VIEW_DEFICIT)
        execs = _roles(*EXEC_ROLES)
        return [
            # ---- hamma AI rollari ----
            Tool("change_language", "Foydalanuvchi tilini darhol almashtirish (tasdiqlashsiz).",
                 {"language": {"type": S, "description": "uz yoki ru"}}, ["language"], ai_allowed, self._t_lang),
            # ---- o'qish ----
            Tool("search_products", "Ombordagi mahsulotni nomi bo'yicha qidirish: qoldiq, birlik, kategoriya, "
                 "Мин. остаток, tanqisligi (ruxsat bo'lsa narx).", {"query": {"type": S, "description": "Nom"}},
                 ["query"], read_stock, self._t_search),
            Tool("low_stock_products", "Tanqis mahsulotlar (qoldiq ≤ Мин. остаток).", {}, [], read_stock, self._t_low),
            Tool("products_in_category", "Kategoriya mahsulotlari va qoldiqlari; kategoriya bo'sh bo'lsa — "
                 "kategoriyalar ro'yxati.", {"category": {"type": S, "description": "Kategoriya"}}, [], read_stock,
                 self._t_category),
            Tool("stock_value", "Ombordagi jami tovar qiymati va kategoriyalar bo'yicha qiymat (so'm).", {}, [],
                 execs, self._t_value),
            Tool("unpriced_inflows", "«Приход» dagi narxi kiritilmagan kirimlar.", {}, [], execs, self._t_unpriced),
            Tool("new_products_log", "🆕 Davrda yangi qo'shilgan mahsulotlar (kim, qachon, kategoriya).", period,
                 ["start_date"], execs, self._t_new),
            Tool("warehouse_movements", "Davr bo'yicha kirim (Приход) va chiqim (Расход): soni, summasi, eng katta "
                 "mahsulotlar; bugungi qatorlar ham.", period, ["start_date"], execs, self._t_flows),
            Tool("shop_expenses", "🏪 Do'kon xarajati davr bo'yicha: jami, kunlar, summa kiritilmagan kunlar.",
                 period, ["start_date"], _perm(Permission.VIEW_EXPENSES, Permission.MARK_EXPENSES), self._t_shop),
            Tool("other_expenses", "🧾 Boshqa xarajatlar (Прочие расходы) davr bo'yicha: jami, kategoriyalar.",
                 period, ["start_date"], _perm(Permission.VIEW_EXPENSES, Permission.MANAGE_WAREHOUSE),
                 self._t_other),
            Tool("attendance_summary", "Davomat davr bo'yicha: har bir ishchi necha kun keldi / kelmadi.",
                 period, ["start_date"], _perm(Permission.VIEW_ATTENDANCE), self._t_att),
            Tool("payroll_summary", "Oy bo'yicha maosh hisobi (davomat asosida): oylik, ushlanma, to'lanadigan.",
                 {"year": {"type": N, "description": "Yil"}, "month": {"type": N, "description": "Oy (1-12)"}},
                 [], _perm(Permission.VIEW_ATTENDANCE), self._t_payroll),
            Tool("find_entries", "Tahrirlash / o'chirish uchun yozuvlarni topish: mahsulot va sana bo'yicha "
                 "(sana bo'lmasa — bugungilar, bo'lmasa oxirgilar). Natijadagi `row` raqami draft_edit_entry / "
                 "draft_delete_entry uchun kerak. shop uchun sana kerak.",
                 {"kind": kind, "product": {"type": S, "description": "Mahsulot yoki izoh (ixtiyoriy)"}, "date": day},
                 ["kind"], _roles(*WRITE_ROLES), self._t_find),
            # ---- yozish: faqat qoralama, foydalanuvchi ✅ bosadi ----
            Tool("draft_inflow", "📥 Kirim (Приход) qoralamasi (har bir mahsulot uchun alohida chaqiring). Miqdor "
                 "dona/birlikda yoki karobkada (unit_mode=box). Narx aytilmasa — oxirgi narx olinadi; «narxsiz» "
                 "deyilsa — 0.",
                 {"product": product, "quantity": {"type": N, "description": "Miqdor"},
                  "unit_mode": {"type": S, "description": "unit (dona/birlik) yoki box (karobka)"},
                  "price_per_unit": {"type": N, "description": "1 birlik narxi, so'm (0 — narxi keyin)"},
                  "price_per_box": {"type": N, "description": "1 karobka narxi, so'm (ixtiyoriy)"},
                  "supplier": {"type": S, "description": "Do'kon / yetkazib beruvchi"}},
                 ["product", "quantity", "supplier"], _writer(Permission.MANAGE_WAREHOUSE), self._w_inflow, True),
            Tool("draft_outflow", "📤 Chiqim (Расход) qoralamasi: qoldiq tekshiriladi (har bir mahsulot uchun "
                 "alohida chaqiring).",
                 {"product": product, "quantity": {"type": N, "description": "Miqdor (birlikda)"},
                  "recipient": {"type": S, "description": "Oluvchi usta (Ответственный)"},
                  "purpose": {"type": S, "description": "Buyurtma / sex / izoh (ixtiyoriy)"}},
                 ["product", "quantity", "recipient"], _roles(*OUTFLOW_ROLES), self._w_outflow, True),
            Tool("draft_edit_entry", "✏️ Bugungi yoki o'tgan kundagi yozuvni tahrirlash (avval find_entries). "
                 "inflow: quantity, price_per_unit, party (do'kon), date; outflow: quantity, party (oluvchi), purpose, "
                 "date; other: amount, category, description, date. Do'kon (shop) uchun draft_shop_expense.",
                 {"kind": kind, "row": {"type": N, "description": "find_entries dagi row"},
                  "quantity": {"type": N, "description": "Yangi miqdor"},
                  "price_per_unit": {"type": N, "description": "Yangi narx (0 — narxsiz)"},
                  "party": {"type": S, "description": "Yangi do'kon / oluvchi"},
                  "purpose": {"type": S, "description": "Yangi izoh (chiqim)"},
                  "new_date": {"type": S, "description": "Yangi sana YYYY-MM-DD"},
                  "amount": {"type": S, "description": "Yangi summa (other)"},
                  "category": {"type": S, "description": "Yangi kategoriya (other)"},
                  "description": {"type": S, "description": "Yangi izoh (other)"}},
                 ["kind", "row"], _writer(Permission.MANAGE_WAREHOUSE), self._w_edit, True),
            Tool("draft_delete_entry", "🗑 Yozuvni butunlay o'chirish (avval find_entries). inflow / outflow / "
                 "other — row bilan; shop — date bilan (kun summasi o'chiriladi).",
                 {"kind": kind, "row": {"type": N, "description": "find_entries dagi row"}, "date": day,
                  "reason": {"type": S, "description": "Sabab (Bossga yuborilgan do'kon kuni uchun majburiy)"}},
                 ["kind"], _writer(Permission.MANAGE_WAREHOUSE), self._w_delete, True),
            Tool("draft_new_product", "➕ Yangi mahsulot qoralamasi. Kategoriya standartlardan (Фурнитура, "
                 "Крепеж / Метизы, Клей и Химия, Поролон и Обивка, Расходники, Инструменты, Кромка ПВХ); "
                 "Мин. остаток > 0 majburiy.",
                 {"name": {"type": S, "description": "Mahsulot nomi"},
                  "category": {"type": S, "description": "Kategoriya (bo'sh — avtomatik)"},
                  "unit": {"type": S, "description": "O'lchov birligi (шт, кг, пара, рулон, пачка ...)"},
                  "min_stock": {"type": N, "description": "Мин. остаток (> 0)"},
                  "pack": {"type": N, "description": "1 karobkadagi dona (ixtiyoriy, standart 1)"}},
                 ["name", "unit", "min_stock"], _writer(Permission.MANAGE_WAREHOUSE), self._w_product, True),
            Tool("draft_rename_product", "✏️ Mahsulot nomini barcha varaqlarda o'zgartirish.",
                 {"product": product, "new_name": {"type": S, "description": "Yangi nom"}}, ["product", "new_name"],
                 _writer(Permission.MANAGE_WAREHOUSE), self._w_rename, True),
            Tool("draft_delete_product", "🗑 Mahsulotni katalogdan (Остатки и Расходы va Справочник) o'chirish.",
                 {"product": product}, ["product"], _writer(Permission.MANAGE_WAREHOUSE), self._w_del_product, True),
            Tool("draft_stock_adjustment", "⚖️ Qoldiqni to'g'rilash qoralamasi: +N qo'shadi, -N ayiradi.",
                 {"product": product, "delta": {"type": N, "description": "+N yoki -N"}},
                 ["product", "delta"], _writer(Permission.MANAGE_WAREHOUSE), self._w_adjust, True),
            Tool("draft_set_price", "💰 Narxi kiritilmagan kirimga narx belgilash qoralamasi.",
                 {"product": product, "price_per_unit": {"type": N, "description": "1 birlik narxi, so'm"},
                  "date": {"type": S, "description": "Kirim sanasi YYYY-MM-DD (bir nechta bo'lsa)"}},
                 ["product", "price_per_unit"], _writer(Permission.MANAGE_WAREHOUSE), self._w_price, True),
            Tool("draft_shop_expense", "🏪 Kunlik do'kon xarajati (yangi yoki o'zgartirish; kun summasi "
                 "almashtiriladi). Qisqa yozuv: 450 = 450 000. Bossga yuborilgan kun uchun sabab (reason) majburiy.",
                 {"amount": {"type": S, "description": "Summa, foydalanuvchi yozganidek"}, "date": day,
                  "reason": {"type": S, "description": "Tuzatish sababi (Bossga yuborilgan kun uchun)"}},
                 ["amount"], _writer(Permission.MARK_EXPENSES), self._w_shop, True),
            Tool("draft_other_expense", "🧾 Boshqa xarajat (Прочие расходы) qoralamasi. Kategoriyalar: Топливо / "
                 "Бензин, Офис / Помещение, Транспорт / Грузы, Ремонт, Прочее. Qisqa yozuv: 450 = 450 000.",
                 {"amount": {"type": S, "description": "Summa, foydalanuvchi yozganidek"},
                  "category": {"type": S, "description": "Kategoriya"},
                  "description": {"type": S, "description": "Nima uchun"}, "date": day},
                 ["amount", "description"], _writer(Permission.MANAGE_WAREHOUSE), self._w_other, True),
            Tool("draft_directory", "👷 Usta yoki 📏 o'lchov birligini Справочник ga qo'shish / o'chirish.",
                 {"kind": {"type": S, "description": "master yoki unit"},
                  "action": {"type": S, "description": "add yoki delete"},
                  "value": {"type": S, "description": "Nomi"}}, ["kind", "action", "value"],
                 _writer(Permission.MANAGE_WAREHOUSE), self._w_directory, True),
        ]

    def tools_for(self, role: Optional[Role]) -> List[Tool]:
        return [t for t in self._tools.values() if t.allow(role)]

    def _period(self, args: Dict[str, Any]):
        today = self.today()
        end = _parse_day(args.get("end_date"), today) or today
        start = _parse_day(args.get("start_date"), end) or end
        if start > end:
            start, end = end, start
        if (end - start).days > MAX_RANGE_DAYS:
            start = end - timedelta(days=MAX_RANGE_DAYS)
        return start, end

    @staticmethod
    def _product_row(p, role: Optional[Role] = None) -> Dict[str, Any]:
        row = {"name": p.name, "category": p.category, "unit": p.unit, "stock": _r(p.stock),
               "min_norm": _r(p.min_stock), "low": bool(p.is_low), "pack": _r(p.pack)}
        if role in PRICE_ROLES:                       # 👷 ustaga narx va pul ko'rsatilmaydi
            row["avg_price_sum"] = _r(p.avg_price)
            row["stock_value_sum"] = _r(p.value or p.stock * p.avg_price)
        return row

    async def _resolve(self, query: str):
        """(Product | None, nomzodlar). Aniq / qism-so'z / o'xshash; bir nechta bo'lsa — nomzodlar."""
        catalog = await self._wh.catalog(force=True)
        names = [p.name for p in catalog]
        by = {p.name: p for p in catalog}
        exact = exact_match(query, names)
        if exact:
            return by[exact], []
        q = normalize(query).replace(" ", "")
        hits = sorted((n for n in names if q and q in normalize(n).replace(" ", "")), key=len)
        hits = hits or search_products(query, names, limit=3)
        if len(hits) == 1:
            return by[hits[0]], []
        return None, hits[:6]

    # ---- til ----
    async def _t_lang(self, ctx: Ctx, language: str = "") -> Any:
        code = str(language or "").strip().lower()[:2]
        code = {"ру": "ru", "uz": "uz", "ru": "ru", "o'": "uz", "ўз": "uz", "уз": "uz"}.get(code, code)
        if code not in ("uz", "ru"):
            return {"error": "language must be uz or ru"}
        ctx.new_lang = code
        return {"status": "language_changed", "language": code,
                "note": "Reply with one short confirmation sentence in the NEW language."}

    # ---- o'qish ----
    async def _t_search(self, ctx: Ctx, query: str = "") -> Any:
        catalog = await self._wh.catalog()
        names = [p.name for p in catalog]
        q = normalize(query).replace(" ", "")
        hits = [n for n in names if q and q in normalize(n).replace(" ", "")][:10] or search_products(query, names, 5)
        by = {p.name: p for p in catalog}
        return {"matches": [self._product_row(by[n], ctx.role) for n in hits if n in by]}

    async def _t_low(self, ctx: Ctx) -> Any:
        low = sorted(await self._wh.low_items(), key=lambda p: p.stock / (p.min_stock or 1))
        return {"count": len(low), "items": [self._product_row(p, ctx.role) for p in low[:40]]}

    async def _t_category(self, ctx: Ctx, category: str = "") -> Any:
        catalog = await self._wh.catalog()
        if not category:
            counts: Dict[str, int] = {}
            for p in catalog:
                counts[p.category or "—"] = counts.get(p.category or "—", 0) + 1
            return {"categories": counts, "products_total": len(catalog)}
        k = normalize(category)
        items = [p for p in catalog if k and k in normalize(p.category or "")]
        return {"category": category, "count": len(items),
                "items": [self._product_row(p, ctx.role) for p in items[:60]]}

    async def _t_value(self, ctx: Ctx) -> Any:
        by: Dict[str, float] = {}
        for p in await self._wh.catalog():
            by[p.category or "—"] = by.get(p.category or "—", 0.0) + (p.value or p.stock * p.avg_price)
        return {"total_sum": _r(sum(by.values())), "by_category_sum": {k: _r(v) for k, v in by.items()}}

    async def _t_unpriced(self, ctx: Ctx) -> Any:
        rows = await self._wh.unpriced()
        return {"count": len(rows), "items": [{"date": r.day.isoformat() if r.day else "", "name": r.name,
                                               "qty": _r(r.qty), "unit": r.unit, "supplier": r.supplier}
                                              for r in rows[:40]]}

    async def _t_new(self, ctx: Ctx, **args) -> Any:
        log = getattr(self._wh, "product_log", None)
        if log is None:
            return {"error": "not configured"}
        start, end = self._period(args)
        items = await log.between(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "count": len(items),
                "items": [{"date": i.day, "name": i.name, "category": i.category, "unit": i.unit,
                           "min": _r(i.min_stock), "by": i.user_name} for i in items[:40]]}

    async def _t_flows(self, ctx: Ctx, **args) -> Any:
        start, end = self._period(args)
        summary = await self._flows.summary(start, end) if self._flows else {}
        out: Dict[str, Any] = {"period": [start.isoformat(), end.isoformat()]}
        for key, label in (("inflow", "prixod"), ("outflow", "rasxod")):
            s = summary.get(key)
            out[label] = {"rows": s.count if s else 0, "sum": _r(s.amount) if s else 0,
                          "top": [{"name": n, "qty": _r(q), "sum": _r(a)} for n, q, a in (s.top if s else [])]}
        today = self.today()
        if self._flows and start <= today <= end:            # bugungi qatorlar kunlik yopilishdan oldin ham
            try:
                deltas = await self._flows.preview(today)
                stored_today = await self._flows.summary(today, today)
                for key, label in (("inflow", "prixod"), ("outflow", "rasxod")):
                    d, already = deltas.get(key), stored_today.get(key)
                    if d and d.new_rows and not (already and already.count):
                        out[label]["rows"] += len(d.new_rows)
                        out[label]["sum"] = _r(out[label]["sum"] + sum(r.amount or 0 for r in d.new_rows))
            except Exception:
                logger.warning("AI: bugungi kirim/chiqimni o'qib bo'lmadi", exc_info=True)
        return out

    async def _t_shop(self, ctx: Ctx, **args) -> Any:
        start, end = self._period(args)
        st = await self._shop.period_stats(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "total_sum": _r(st.total),
                "per_day_sum": {d.isoformat(): _r(v) for d, v in sorted(st.per_day.items())},
                "days_without_amount": [d.isoformat() for d in st.missing][:31]}

    async def _t_other(self, ctx: Ctx, **args) -> Any:
        other = getattr(self._wh, "other", None)
        if other is None:
            return {"error": "not configured"}
        start, end = self._period(args)
        s = await other.summary(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "total_sum": _r(s.total), "count": s.count,
                "by_category_sum": {k: _r(v) for k, v in s.by_category.items()},
                "rows": [{"date": r.day.isoformat(), "category": r.category, "description": r.description,
                          "sum": _r(r.amount)} for r in s.rows[:30]]}

    async def _t_att(self, ctx: Ctx, **args) -> Any:
        start, end = self._period(args)
        st = await self._att.period_stats(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "recorded_days": st.recorded_days,
                "work_days": st.work_days,
                "workers": [{"name": w.name, "came": w.came, "absent": w.absent, "excused": w.excused}
                            for w in st.workers]}

    async def _t_payroll(self, ctx: Ctx, year: Any = None, month: Any = None) -> Any:
        today = self.today()
        y, m = int(_num(year) or today.year), int(_num(month) or today.month)
        if not 1 <= m <= 12:
            return {"error": "bad month"}
        until = today if (y, m) == (today.year, today.month) else (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        rep = await self._att.month_payroll(y, m, until)
        if not rep.tab_found:
            return {"error": "attendance sheet not found"}
        out: Dict[str, Any] = {"year": y, "month": m, "work_days": rep.work_days}
        if rep.pays is None:
            out["error"] = "salary columns not found"
        else:
            out["workers"] = [{"name": p.name, "salary": p.salary, "deduct_days": p.deduct_days,
                               "deduction": p.deduction, "payable": p.payable} for p in rep.pays]
            out["total_payable"] = sum(p.payable or 0 for p in rep.pays)
        return out

    @staticmethod
    def _entry_json(e) -> Dict[str, Any]:
        out = {"row": e.row, "date": e.day.isoformat() if e.day else "", "name": e.name, "qty": _r(e.qty),
               "party": e.party}
        if e.kind == "in":
            out.update(unit=e.unit, price=_r(e.price) if e.price else 0)
        else:
            out.update(purpose=e.purpose)
        return out

    async def _entries(self, kind: str, product: str = "", day: Optional[date] = None, limit: int = 10):
        entries = await self._wh.flow_entries("in" if kind == "inflow" else "out", product, day, limit=60)
        if day is None:                                      # sana aytilmagan: avval bugungilar
            today = [e for e in entries if e.day == self.today()]
            entries = today or entries
        return entries[:limit]

    async def _other_rows(self, text: str = "", day: Optional[date] = None):
        other = getattr(self._wh, "other", None)
        if other is None:
            return []
        rows = await other.rows(force=True)
        if day is not None:
            rows = [r for r in rows if r.day == day]
        if text:
            q = normalize(text)
            rows = [r for r in rows if q and (q in normalize(r.description) or q in normalize(r.category))] or rows
        return sorted(rows, key=lambda r: (r.day, r.row), reverse=True)

    async def _t_find(self, ctx: Ctx, kind: str = "", product: str = "", date: str = "") -> Any:
        day = _parse_day(date, None)
        kind = (kind or "").strip().lower()
        if kind in ("inflow", "outflow"):
            entries = await self._entries(kind, product, day)
            return {"kind": kind, "count": len(entries), "entries": [self._entry_json(e) for e in entries]}
        if kind == "other":
            rows = (await self._other_rows(product, day))[:10]
            return {"kind": kind, "entries": [{"row": r.row, "date": r.day.isoformat(), "category": r.category,
                                               "description": r.description, "amount": _r(r.amount)} for r in rows]}
        if kind == "shop":
            d = day or self.today()
            found = await self._shop.get_day(d)
            amount = found[1] if found and found[0] else None
            sent = bool(self._pending and await self._pending.is_sent("shop", d.isoformat()) is True)
            return {"kind": kind, "date": d.isoformat(), "amount": amount, "sent_to_boss": sent}
        return {"error": "kind must be inflow | outflow | other | shop"}

    # ---- yozish: qoralamalar ----
    def _draft(self, ctx: Ctx, kind: str, payload: Dict[str, Any], card: str) -> Dict[str, Any]:
        if len(ctx.drafts) >= MAX_DRAFTS_PER_ASK:
            return {"error": "too many drafts in one message"}
        d = self.drafts.add(ctx.user_id, kind, payload, card)
        ctx.drafts.append(d)
        return {"status": "awaiting_user_confirmation", "draft_id": d.id,
                "note": "Saved NOT yet. The user must press ✅ Tasdiqlash on the card."}

    async def _need_product(self, query: str):
        p, cands = await self._resolve(query)
        if p is None:
            return None, ({"error": "ambiguous", "candidates": cands} if cands else
                          {"error": "not_found", "hint": "Offer draft_new_product (ask unit and min_stock)."})
        return p, None

    async def _w_inflow(self, ctx: Ctx, product: str = "", quantity: Any = None, unit_mode: str = "unit",
                        price_per_unit: Any = None, price_per_box: Any = None, supplier: str = "") -> Any:
        t = get_texts(ctx.lang)
        p, err = await self._need_product(product)
        if err:
            return err
        q = _num(quantity)
        if not q or q <= 0:
            return {"error": "quantity must be > 0"}
        box = str(unit_mode or "").lower().startswith(("box", "kar", "кор", "пач", "упак")) and p.pack > 1
        qty = q * p.pack if box else q
        price = _num(price_per_unit)
        box_price = _num(price_per_box)
        if box_price and box_price > 0:
            price = box_price / (p.pack if p.pack > 1 else 1)
        last_used = False
        if price is None and box_price is None:              # narx aytilmadi: oxirgi narx
            last = await self._wh.last_price(p.name)
            if last:
                price, last_used = last, True
        price = price if price and price > 0 else None
        supplier = _clean(supplier, 60)
        if not supplier:
            from app.services.warehouse import SUPPLIER
            return {"error": "supplier required", "known_suppliers": await self._wh.top_names(SUPPLIER, 8)}
        boxes = f" ({_n(q)} {t['wh_unit_box']} × {_n(p.pack)})" if box else ""
        cur = "сум" if ctx.lang == "ru" else "so'm"
        price_text = (f"{_n(price)} {cur} · {'итого' if ctx.lang == 'ru' else 'jami'} {_n(price * qty)}"
                      + (" (🔁)" if last_used else "")) if price else t["wh_price_later_mark"]
        card = t["ai_d_inflow"].format(name=html.escape(p.name), qty=_n(qty), unit=html.escape(p.unit), boxes=boxes,
                                       price=price_text, supplier=html.escape(supplier))
        res = self._draft(ctx, "inflow", {"name": p.name, "unit": p.unit, "qty": qty, "price": price,
                                          "supplier": supplier, "boxes": q if box else 0.0, "pack": p.pack}, card)
        if last_used:
            res["price_used"] = {"last_price": _r(price)}
        return res

    async def _w_outflow(self, ctx: Ctx, product: str = "", quantity: Any = None, recipient: str = "",
                         purpose: str = "") -> Any:
        t = get_texts(ctx.lang)
        p, err = await self._need_product(product)
        if err:
            return err
        qty = _num(quantity)
        if not qty or qty <= 0:
            return {"error": "quantity must be > 0"}
        reserved = sum(float(d.payload.get("qty") or 0) for d in ctx.drafts
                       if d.kind == "outflow" and d.payload.get("name") == p.name)
        available = p.stock - reserved
        if qty > available + 1e-9:
            return {"error": "insufficient_stock", "stock": _r(available), "unit": p.unit}
        recipient = _clean(recipient, 60)
        if not recipient:
            from app.services.warehouse import RECIPIENT
            return {"error": "recipient required", "known_recipients": await self._wh.top_names(RECIPIENT, 8)}
        purpose = _clean(purpose, 100)
        card = t["ai_d_outflow"].format(name=html.escape(p.name), qty=_n(qty), unit=html.escape(p.unit),
                                        stock=_n(available), after=_n(available - qty), who=html.escape(recipient),
                                        purpose=html.escape(purpose) or "—")
        return self._draft(ctx, "outflow", {"name": p.name, "unit": p.unit, "qty": qty, "recipient": recipient,
                                            "purpose": purpose}, card)

    async def _find_entry(self, kind: str, row: Any):
        r = int(_num(row) or 0)
        entries = await self._wh.flow_entries("in" if kind == "inflow" else "out", "", None, limit=100000)
        return next((e for e in entries if e.row == r), None)

    async def _w_edit(self, ctx: Ctx, kind: str = "", row: Any = None, quantity: Any = None,
                      price_per_unit: Any = None, party: str = "", purpose: str = "", new_date: str = "",
                      amount: Any = None, category: str = "", description: str = "") -> Any:
        t = get_texts(ctx.lang)
        kind = (kind or "").strip().lower()
        changes: List[str] = []
        fields: Dict[str, Any] = {}
        nd = _parse_day(new_date, None)
        if nd and nd > self.today():
            return {"error": "future date"}
        if kind in ("inflow", "outflow"):
            e = await self._find_entry(kind, row)
            if e is None:
                return {"error": "row not found — call find_entries again"}
            q = _num(quantity)
            if q is not None:
                if q <= 0:
                    return {"error": "quantity must be > 0 (to remove the row use draft_delete_entry)"}
                if kind == "outflow" and q > e.qty:
                    p = await self._wh.product(e.name, force=True)
                    if p is not None and q - e.qty > p.stock + 1e-9:
                        return {"error": "insufficient_stock", "stock": _r(p.stock)}
                fields["qty"] = q
                changes.append(t["ai_ch_qty"].format(old=_n(e.qty), new=_n(q)))
            if kind == "inflow" and price_per_unit is not None and _num(price_per_unit) is not None:
                pr = _num(price_per_unit) or None
                fields["price"] = pr
                changes.append(t["ai_ch_price"].format(old=_n(e.price) if e.price else "—",
                                                       new=_n(pr) if pr else t["wh_price_later_mark"]))
            if party:
                fields["party"] = _clean(party, 60)
                changes.append(t["ai_ch_party"].format(old=html.escape(e.party or "—"), new=html.escape(fields["party"])))
            if kind == "outflow" and purpose:
                fields["purpose"] = _clean(purpose, 100)
                changes.append(t["ai_ch_purpose"].format(old=html.escape(e.purpose or "—"),
                                                         new=html.escape(fields["purpose"])))
            if nd:
                fields["iso"] = nd.isoformat()
                changes.append(t["ai_ch_date"].format(old=e.day.strftime("%d.%m.%Y") if e.day else "—",
                                                      new=nd.strftime("%d.%m.%Y")))
            if not fields:
                return {"error": "nothing to change"}
            card = t["ai_d_edit_in" if kind == "inflow" else "ai_d_edit_out"].format(
                date=e.day.strftime("%d.%m.%Y") if e.day else "—", row=e.row, name=html.escape(e.name),
                changes="\n".join(changes))
            return self._draft(ctx, "edit_in" if kind == "inflow" else "edit_out",
                               {"expect": e.expect(), "fields": fields, "name": e.name}, card)
        if kind == "other":
            from app.services.other_expenses import DEFAULT_CATEGORIES as OTHER
            rows = await self._other_rows()
            r = next((x for x in rows if x.row == int(_num(row) or 0)), None)
            if r is None:
                return {"error": "row not found — call find_entries again"}
            if amount is not None and str(amount).strip():
                v = _money(amount)
                if not v or v <= 0:
                    return {"error": "amount not understood"}
                fields["amount"] = v
                changes.append(t["ai_ch_amount"].format(old=_n(r.amount), new=_n(v)))
            if category:
                k = normalize(category)
                cat = next((c for c, uz, ru in OTHER if k and (k in normalize(c) or k in normalize(uz)
                                                               or k in normalize(ru))), None)
                if cat is None:
                    return {"error": "unknown category", "categories": [c for c, _u, _r2 in OTHER]}
                fields["category"] = cat
                changes.append(t["ai_ch_category"].format(old=html.escape(r.category), new=html.escape(cat)))
            if description:
                fields["description"] = _clean(description, 200)
                changes.append(t["ai_ch_desc"].format(old=html.escape(r.description),
                                                      new=html.escape(fields["description"])))
            if nd:
                fields["iso"] = nd.isoformat()
                changes.append(t["ai_ch_date"].format(old=r.day.strftime("%d.%m.%Y"), new=nd.strftime("%d.%m.%Y")))
            if not fields:
                return {"error": "nothing to change"}
            card = t["ai_d_edit_other"].format(date=r.day.strftime("%d.%m.%Y"), desc=html.escape(r.description),
                                               changes="\n".join(changes))
            return self._draft(ctx, "edit_other", {"expect": r.expect(), "fields": fields}, card)
        if kind == "shop":
            return {"error": "use draft_shop_expense with the date to change a store expense"}
        return {"error": "kind must be inflow | outflow | other"}

    async def _w_delete(self, ctx: Ctx, kind: str = "", row: Any = None, date: str = "", reason: str = "") -> Any:
        t = get_texts(ctx.lang)
        kind = (kind or "").strip().lower()
        if kind in ("inflow", "outflow"):
            e = await self._find_entry(kind, row)
            if e is None:
                return {"error": "row not found — call find_entries again"}
            cur = "сум" if ctx.lang == "ru" else "so'm"
            card = t["ai_d_del_in" if kind == "inflow" else "ai_d_del_out"].format(
                date=e.day.strftime("%d.%m.%Y") if e.day else "—", row=e.row, name=html.escape(e.name),
                qty=_n(e.qty), unit=html.escape(e.unit), party=html.escape(e.party or "—"),
                purpose=html.escape(e.purpose or "—"),
                price=f"{_n(e.price)} {cur}" if e.price else t["wh_price_later_mark"])
            return self._draft(ctx, "del_in" if kind == "inflow" else "del_out",
                               {"expect": e.expect(), "name": e.name}, card)
        if kind == "other":
            rows = await self._other_rows()
            r = next((x for x in rows if x.row == int(_num(row) or 0)), None)
            if r is None:
                return {"error": "row not found — call find_entries again"}
            from app.services.other_expenses import category_label as other_label
            card = t["ai_d_del_other"].format(date=r.day.strftime("%d.%m.%Y"), cat=html.escape(other_label(r.category, ctx.lang)),
                                              desc=html.escape(r.description), amount=_n(r.amount))
            return self._draft(ctx, "del_other", {"expect": r.expect()}, card)
        if kind == "shop":
            d = _parse_day(date, self.today())
            found = await self._shop.get_day(d)
            old = found[1] if found and found[0] else None
            if not old:
                return {"error": "no store expense on this date"}
            sent = bool(self._pending and await self._pending.is_sent("shop", d.isoformat()) is True)
            reason = _clean(reason, 200)
            if sent and not reason:
                return {"error": "reason required: this day was already reported to the Boss"}
            card = t["ai_d_clear_shop"].format(date=d.strftime("%d.%m.%Y"), old=_n(old))
            if sent:
                card += t["ai_d_shop_audit"].format(reason=html.escape(reason))
            return self._draft(ctx, "clear_shop", {"iso": d.isoformat(), "old": old, "audit": sent,
                                                   "reason": reason}, card)
        return {"error": "kind must be inflow | outflow | other | shop"}

    async def _w_product(self, ctx: Ctx, name: str = "", category: str = "", unit: str = "", min_stock: Any = None,
                         pack: Any = None) -> Any:
        t = get_texts(ctx.lang)
        name = _clean(name, 100)
        if len(name) < 2:
            return {"error": "name required"}
        catalog = await self._wh.catalog(force=True)
        dup = exact_match(name, [p.name for p in catalog])
        if dup:
            return {"error": "already_exists", "name": dup}
        cats = await self._wh.all_categories()
        unit = _clean(unit, 15)
        if kromka.is_kromka(name):
            size = kromka.parse_size(name)
            if not size:
                return {"error": "kromka size required", "sizes": list(kromka.SIZES)}
            name, cat, unit = kromka.build_name(kromka.color_of(name) or "ПВХ", size), kromka.CATEGORY, kromka.UNIT
        else:
            cat = None
            if category:
                k = category.strip().casefold()
                cat = next((c for c in cats if k in (c.casefold(), cat_label(c, ctx.lang).casefold())
                            or k in c.casefold()), None)
            cat = cat or suggest_category(name, [(p.name, p.category) for p in catalog])
            if not cat:
                return {"error": "category required", "categories": cats}
        if not unit:
            return {"error": "unit required", "units": await self._wh.units()}
        mn = _num(min_stock)
        if not mn or mn <= 0:
            return {"error": "min_stock must be > 0 (mandatory)"}
        pk = _num(pack) or 1.0
        pk = float(int(pk)) if pk >= 1 else 1.0
        card = t["ai_d_product"].format(name=html.escape(name), cat=html.escape(cat_label(cat, ctx.lang)),
                                        unit=html.escape(unit), pack=_n(pk), min=_n(mn))
        return self._draft(ctx, "product", {"name": name, "category": cat, "unit": unit, "pack": pk, "min": mn},
                           card)

    async def _w_rename(self, ctx: Ctx, product: str = "", new_name: str = "") -> Any:
        t = get_texts(ctx.lang)
        p, err = await self._need_product(product)
        if err:
            return err
        new = _clean(new_name, 100)
        if len(new) < 2:
            return {"error": "new_name required"}
        dup = exact_match(new, [x.name for x in await self._wh.catalog()])
        if dup and dup != p.name:
            return {"error": "already_exists", "name": dup}
        card = t["ai_d_rename"].format(old=html.escape(p.name), new=html.escape(new))
        return self._draft(ctx, "rename", {"old": p.name, "new": new}, card)

    async def _w_del_product(self, ctx: Ctx, product: str = "") -> Any:
        t = get_texts(ctx.lang)
        p, err = await self._need_product(product)
        if err:
            return err
        card = t["ai_d_del_product"].format(name=html.escape(p.name), stock=_n(p.stock), unit=html.escape(p.unit))
        return self._draft(ctx, "del_product", {"name": p.name}, card)

    async def _w_adjust(self, ctx: Ctx, product: str = "", delta: Any = None) -> Any:
        t = get_texts(ctx.lang)
        p, err = await self._need_product(product)
        if err:
            return err
        d = _num(delta)
        if not d:
            return {"error": "delta must be non-zero (+N or -N)"}
        if p.stock + d < -1e-9:
            return {"error": "stock would become negative", "stock": _r(p.stock)}
        card = t["ai_d_adjust"].format(name=html.escape(p.name), stock=_n(p.stock), after=_n(p.stock + d),
                                       unit=html.escape(p.unit), delta=("+" if d > 0 else "−") + _n(abs(d)))
        return self._draft(ctx, "adjust", {"name": p.name, "unit": p.unit, "delta": d}, card)

    async def _w_price(self, ctx: Ctx, product: str = "", price_per_unit: Any = None, date: str = "") -> Any:
        t = get_texts(ctx.lang)
        price = _num(price_per_unit)
        if not price or price <= 0:
            return {"error": "price must be > 0"}
        rows = await self._wh.unpriced()
        if not rows:
            return {"error": "no unpriced inflows"}
        names = sorted({r.name for r in rows})
        exact = exact_match(product, names)
        if exact:
            name = exact
        else:
            q = normalize(product).replace(" ", "")
            hits = [n for n in names if q and q in normalize(n).replace(" ", "")] or search_products(product, names, 3)
            if len(hits) != 1:
                return {"error": "ambiguous" if hits else "not_found_in_unpriced", "unpriced_products": hits or names[:15]}
            name = hits[0]
        mine = [r for r in rows if r.name == name]
        if date:
            want = _parse_day(date, self.today())
            mine = [r for r in mine if r.day == want] or mine
        if len(mine) > 1:
            return {"error": "several unpriced inflows, ask which date",
                    "entries": [{"date": r.day.isoformat() if r.day else "", "qty": _r(r.qty)} for r in mine]}
        r = mine[0]
        card = t["ai_d_price"].format(name=html.escape(r.name), qty=_n(r.qty), unit=html.escape(r.unit),
                                      date=r.day.strftime("%d.%m.%Y") if r.day else "—", price=_n(price),
                                      total=_n(price * r.qty))
        return self._draft(ctx, "price", {"row": r.row, "name": r.name, "qty": r.qty,
                                          "iso": r.day.isoformat() if r.day else "", "price": price}, card)

    async def _w_shop(self, ctx: Ctx, amount: Any = None, date: str = "", reason: str = "") -> Any:
        t = get_texts(ctx.lang)
        value = _money(amount)
        if value is None or value <= 0:
            return {"error": "amount not understood"}
        today = self.today()
        day = _parse_day(date, today)
        if day > today:
            return {"error": "future date"}
        found = await self._shop.get_day(day)
        old = found[1] if found and found[0] and found[1] is not None else None
        sent = bool(self._pending is not None and await self._pending.is_sent("shop", day.isoformat()) is True)
        reason = _clean(reason, 200)
        if sent and not reason:
            return {"error": "reason required: this day was already reported to the Boss (ask the reason)"}
        card = t["ai_d_shop"].format(date=day.strftime("%d.%m.%Y"), old=_n(old) if old else "—", amount=_n(value))
        if sent:
            card += t["ai_d_shop_audit"].format(reason=html.escape(reason))
        return self._draft(ctx, "shop", {"iso": day.isoformat(), "amount": value, "old": old or 0, "audit": sent,
                                         "reason": reason}, card)

    async def _w_other(self, ctx: Ctx, amount: Any = None, category: str = "", description: str = "",
                       date: str = "") -> Any:
        from app.services.other_expenses import DEFAULT_CATEGORIES as OTHER
        from app.services.other_expenses import category_label as other_label
        t = get_texts(ctx.lang)
        value = _money(amount)
        if value is None or value <= 0:
            return {"error": "amount not understood"}
        desc = _clean(description, 200)
        if not desc:
            return {"error": "description required"}
        k = normalize(category or "")
        cat = next((c for c, uz, ru in OTHER if k and (k in normalize(c) or k in normalize(uz) or k in normalize(ru))),
                   "Прочее")
        today = self.today()
        day = _parse_day(date, today)
        if day > today:
            return {"error": "future date"}
        card = t["ai_d_other"].format(date=day.strftime("%d.%m.%Y"), cat=html.escape(other_label(cat, ctx.lang)),
                                      desc=html.escape(desc), amount=_n(value))
        return self._draft(ctx, "other", {"iso": day.isoformat(), "category": cat, "description": desc,
                                          "amount": value}, card)

    async def _w_directory(self, ctx: Ctx, kind: str = "", action: str = "", value: str = "") -> Any:
        from app.services.warehouse import MASTER, UNIT_KIND
        t = get_texts(ctx.lang)
        k = (kind or "").strip().lower()
        dk = MASTER if k.startswith(("mas", "ust", "мас")) else UNIT_KIND if k.startswith(("un", "bir", "ед")) else None
        act = (action or "").strip().lower()
        add = act.startswith(("add", "qo", "доб"))
        if dk is None or not (add or act.startswith(("del", "o'ch", "уда", "och"))):
            return {"error": "kind must be master|unit and action add|delete"}
        val = _clean(value, 60 if dk == MASTER else 15)
        if not val:
            return {"error": "value required"}
        existing = await self._wh.directory(dk)
        hit = next((x for x in existing if x.casefold() == val.casefold()), None)
        if add and hit:
            return {"error": "already_exists", "value": hit}
        if not add and not hit:
            return {"error": "not_found", "existing": existing[:30]}
        label = t["ai_dir_master" if dk == MASTER else "ai_dir_unit"]
        card = t["ai_d_dir_add" if add else "ai_d_dir_del"].format(kind=label, value=html.escape(hit or val))
        return self._draft(ctx, "dir_add" if add else "dir_del", {"dk": dk, "value": hit or val}, card)

    async def run_tool(self, name: str, args: Dict[str, Any], ctx: Ctx) -> Any:
        tool = self._tools.get(name)
        if tool is None or not tool.allow(ctx.role):
            return {"error": "tool not allowed for this role"}
        allowed = {k: v for k, v in (args or {}).items() if k in tool.params}
        try:
            result = await tool.run(ctx, **allowed)
        except Exception as e:
            logger.exception("AI vositasi xatosi: %s", name)
            return {"error": type(e).__name__}
        text = json.dumps(result, ensure_ascii=False, default=str)
        return result if len(text) < 12000 else {"truncated": text[:12000]}

    # ---------------- Gemini ----------------
    def _system(self, role: Optional[Role], lang: str) -> str:
        today = self.today()
        t = get_texts(lang)
        refusal = t["ai_offtopic"]
        language = "Russian" if lang == "ru" else "Uzbek (Latin script)"
        caps = _plain(t.get(f"caps_{role.value}", "")) if role else ""
        if role in WRITE_ROLES:
            rights = ("This user MAY create, edit and delete records (today or past dates), but ONLY via draft_* "
                      "tools; nothing is saved until the user presses ✅ Tasdiqlash on each card. Never claim something "
                      "is saved. To edit or delete: call find_entries first, then use the exact `row`. If several rows "
                      "match, list them briefly (date, qty, party) and ask which one. For compound commands call "
                      "several tools in a row (one draft per item). If a tool returns an error (missing supplier / "
                      "recipient / reason, ambiguous product, not enough stock), ask ONE short question.")
        elif role == Role.MASTER:
            rights = ("This user is a Master (Usta): may only view stock / low stock (NO prices, NO money) and create "
                      "outflow drafts (draft_outflow). For anything else (inflow, prices, catalog, expenses, editing "
                      "or deleting records, reports) say politely that it is not available for their role.")
        else:
            rights = ("This user is the Boss: READ-ONLY analytics. If asked to record, edit or delete anything, "
                      "politely say that only the Admin and the Storekeeper record operations.")
        return (
            "You are the voice & text assistant of the «Ziyo Zamir MCHJ» furniture factory Telegram bot. "
            f"Today: {today.isoformat()} ({today.strftime('%A')}), timezone Asia/Tashkent. "
            f"User role: {role.value if role else 'unknown'}. Reply in {language}; if the user writes in another "
            "language, reply in that language.\n"
            "DOMAIN LOCK: answer ONLY about this factory's work: warehouse stock, inflows/outflows (Приход/Расход), "
            "product catalog and prices, store expenses (Do'kon xarajati), other operational expenses (Прочие "
            "расходы), staff attendance and payroll, changing the bot language, and how to use this bot. For ANYTHING "
            "else (general chat, weather, news, jokes, coding, recipes, personal advice, politics, translations, math "
            f"homework, etc.) do not answer it and reply with exactly this one sentence and nothing more: {refusal}\n"
            "HELP: if the user asks what they can do, asks for help ('yordam', 'nima qila olaman', 'qanday "
            "ishlataman', 'помощь') or seems unsure, reply with ONLY these 4-6 short bullets for their role (never "
            f"mention features of other roles):\n{caps}\n"
            "LANGUAGE: if the user asks to switch the language, call change_language at once.\n"
            "Get every number ONLY from the tools; never invent data; if not found, say so. Money in so'm with "
            "space thousands (1 250 000). Resolve 'today', 'yesterday', 'this week', 'last month' from today. Be brief: "
            "at most 6 short lines or bullets, plain text, **bold** allowed.\n" + rights)

    def _declarations(self, role: Optional[Role]):
        from google.genai import types
        decls = []
        for tool in self.tools_for(role):
            params = None
            if tool.params:
                params = types.Schema(
                    type=types.Type.OBJECT,
                    properties={k: types.Schema(type=types.Type.NUMBER if v.get("type") == "number"
                                                else types.Type.STRING, description=v.get("description", ""))
                                for k, v in tool.params.items()},
                    required=list(tool.required) or None)
            decls.append(types.FunctionDeclaration(name=tool.name, description=tool.description, parameters=params))
        return [types.Tool(function_declarations=decls)] if decls else None

    def _config(self, role: Optional[Role], lang: str, model: Optional[str] = None):
        from google.genai import types
        kwargs: Dict[str, Any] = dict(
            system_instruction=self._system(role, lang), tools=self._declarations(role),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        if re.match(r"^gemini-[12]\.", model or self.model or ""):
            kwargs["temperature"] = TEMPERATURE            # Gemini 3+: temperature eskirgan — yuborilmaydi
        return types.GenerateContentConfig(**kwargs)

    async def _generate(self, contents: List[Any], role: Optional[Role], lang: str, state: Dict[str, Any]):
        """Asosiy model -> (429 / 503 / 404) -> zaxira model. `state["model"]` — shu navbatda ishlayotgan model
        (zaxiraga o'tilgach navbat oxirigacha zaxirada qoladi)."""
        from google.genai import errors
        models = [state.get("model") or self.model]
        if models[0] == self.model and self.fallback_model:
            if time.monotonic() < self._primary_down_until:
                models = [self.fallback_model]
            else:
                models.append(self.fallback_model)
        codes: List[Any] = []
        for i, model in enumerate(models):
            try:
                resp = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=self._config(role, lang, model))
                state["model"] = model
                return resp
            except errors.APIError as e:
                code = getattr(e, "code", None)
                codes.append(code)
                if code in FALLBACK_CODES and i + 1 < len(models):
                    logger.warning("Primary model %s unavailable (%s), switching to fallback %s",
                                   model, code, models[i + 1])
                    if code == 429:
                        self._primary_down_until = time.monotonic() + PRIMARY_SKIP
                    continue
                if code == 429 and all(c == 429 for c in codes):
                    raise AgentBusy() from e
                if code in (429, 503) and len(models) == 1:
                    raise AgentBusy() from e
                raise
        raise AgentBusy()

    def _history_for(self, user_id: int) -> Deque[Any]:
        now = time.monotonic()
        ts, hist = self._history.get(user_id, (now, deque(maxlen=HISTORY_TURNS)))
        if now - ts > HISTORY_TTL:
            hist = deque(maxlen=HISTORY_TURNS)
        self._history[user_id] = (now, hist)
        return hist

    def _check_cooldown(self, user_id: int) -> None:
        now = time.monotonic()
        if now - self._last.get(user_id, 0.0) < COOLDOWN:
            raise AgentCooldown()
        self._last[user_id] = now

    async def ask(self, user_id: int, role: Optional[Role], lang: str, text: str = "",
                  audio: Optional[bytes] = None, image: Optional[bytes] = None,
                  image_mime: str = "image/jpeg", name: str = "") -> AgentReply:
        """Javob (Telegram HTML) + tasdiqlash kerak bo'lgan qoralamalar + (bo'lsa) yangi til."""
        if not self.enabled:
            raise RuntimeError("AI agent is disabled")
        if not ai_allowed(role):
            raise AgentDenied()
        from google.genai import types
        self._check_cooldown(user_id)
        ctx = Ctx(user_id, role, lang, name)
        parts = []
        if audio:
            parts.append(types.Part.from_bytes(data=audio, mime_type="audio/ogg"))
        if image:
            parts.append(types.Part.from_bytes(data=image, mime_type=image_mime))
        prompt = (text or "").strip() or ("Listen to the voice message and act on it." if audio else
                                          "What is in this photo (work-related)?" if image else "")
        parts.append(types.Part(text=prompt[:2000]))
        history = self._history_for(user_id)
        contents: List[Any] = list(history) + [types.Content(role="user", parts=parts)]
        answer = ""
        run: Dict[str, Any] = {}
        async with self._sem:
            for _ in range(MAX_ROUNDS):
                resp = await self._generate(contents, role, ctx.new_lang or lang, run)
                calls = list(getattr(resp, "function_calls", None) or [])
                if not calls:
                    answer = (getattr(resp, "text", None) or "").strip()
                    break
                contents.append(resp.candidates[0].content)     # thought signature'lar ham saqlanadi
                replies = []
                for call in calls:
                    logger.info("AI vositasi: %s (user=%s)", call.name, user_id)
                    result = await self.run_tool(call.name, dict(call.args or {}), ctx)
                    replies.append(types.Part.from_function_response(name=call.name, response={"result": result}))
                contents.append(types.Content(role="user", parts=replies))
        out_lang = ctx.new_lang or lang
        if not answer:
            answer = get_texts(out_lang)["ai_draft_hint"] if ctx.drafts else (
                get_texts(out_lang)["ai_lang_done"] if ctx.new_lang else "…")
        note = (text or "").strip() or ("[voice]" if audio else "[photo]")
        history.append(types.Content(role="user", parts=[types.Part(text=note[:500])]))
        history.append(types.Content(role="model", parts=[types.Part(text=answer[:800])]))
        return AgentReply(to_telegram_html(answer), ctx.drafts, ctx.new_lang)
