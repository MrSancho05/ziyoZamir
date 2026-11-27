"""🤖 AI yordamchi: Google Gemini (bepul tarif, standart `gemini-3.5-flash-lite`) + botning o'z funksiyalari.

Kim foydalanadi: FAQAT Boss, Admin va Sklad nazoratchisi (faol rol bo'yicha — 🎭 Rolni sinash ham hisobga
olinadi). Ishchi (worker) uchun Gemini umuman chaqirilmaydi.

Nima qiladi:
- faqat zavod ishi: ombor, kirim/chiqim, katalog va narxlar, do'kon va boshqa xarajatlar, davomat/maosh,
  botdan foydalanish. Boshqa mavzular — bitta qisqa jumla bilan rad etiladi (tokenlar tejaladi);
- o'qish vositalari rollarga `app/core/permissions.py` bo'yicha beriladi;
- yozish (Kirim, Chiqim, yangi mahsulot, qoldiqni to'g'rilash, narx, do'kon / boshqa xarajat) — FAQAT Admin va
  Sklad nazoratchisi uchun va FAQAT qoralama (draft): model hech narsani o'zi yozmaydi, foydalanuvchi
  «✅ Tasdiqlash» ni bosgandan keyin `app/handlers/ai_confirm.py` botning mavjud yozish funksiyalarini chaqiradi.
Tejash: max_output_tokens=450, qisqa suhbat xotirasi (4 ta xabar, 10 daqiqadan keyin o'chadi), bo'sh / 1–2
belgili xabarlarga API chaqirilmaydi. Python 3.9+ bilan mos (google-genai>=1.0.0)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import secrets
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
from app.services.search import exact_match, normalize, search_products
from app.services.shop_grid import parse_shop_amount

logger = logging.getLogger(__name__)

MAX_ROUNDS = 5               # bitta savolda vosita chaqiruvlari soni
COOLDOWN = 3.0               # soniya: bitta foydalanuvchi so'rovlari orasida
HISTORY_TURNS = 4            # suhbat xotirasi: oxirgi 4 ta xabar (2 savol + 2 javob)
HISTORY_TTL = 600.0          # 10 daqiqa jim turilsa xotira o'chadi
DRAFT_TTL = 600.0            # tasdiqlanmagan qoralama 10 daqiqada eskiradi
MAX_DRAFTS_PER_ASK = 5
MAX_OUTPUT_TOKENS = 450
TEMPERATURE = 0.2            # faqat 1.x / 2.x modellarga: Gemini 3+ da temperature eskirgan (e'tiborsiz)
MAX_ANSWER = 3800
MAX_RANGE_DAYS = 366

AI_ROLES = (Role.ADMIN, Role.BOSS, Role.STOREKEEPER)
WRITE_ROLES = (Role.ADMIN, Role.STOREKEEPER)


class AgentBusy(Exception):
    """Bepul tarif limiti (429) yoki band."""


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


def _parse_day(value: Any, default: date) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def _clean(value: Any, limit: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip().lstrip("=+@").strip()
    return s[:limit]


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


# ---------------- qoralamalar (tasdiqlash kerak bo'lgan yozuvlar) ----------------
@dataclass
class Draft:
    id: str
    user_id: int
    kind: str                       # inflow | outflow | product | adjust | price | shop | other
    payload: Dict[str, Any]
    card: str                       # foydalanuvchiga ko'rsatiladigan HTML karta
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > DRAFT_TTL


class DraftStore:
    def __init__(self) -> None:
        self._items: Dict[str, Draft] = {}

    def _cleanup(self) -> None:
        for k in [k for k, d in self._items.items() if d.expired]:
            self._items.pop(k, None)

    def add(self, user_id: int, kind: str, payload: Dict[str, Any], card: str) -> Draft:
        self._cleanup()
        draft = Draft(secrets.token_hex(5), user_id, kind, payload, card)
        self._items[draft.id] = draft
        return draft

    def get(self, draft_id: str) -> Optional[Draft]:
        self._cleanup()
        return self._items.get(draft_id)

    def take(self, draft_id: str) -> Optional[Draft]:
        """Bir marta: ikkinchi bosishda None (ikki marta yozilmaydi)."""
        self._cleanup()
        return self._items.pop(draft_id, None)


# ---------------- vositalar ----------------
@dataclass
class Ctx:
    user_id: int
    role: Optional[Role]
    lang: str
    name: str = ""
    drafts: List[Draft] = field(default_factory=list)


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


def _staff(role: Optional[Role]) -> bool:
    return ai_allowed(role)


def _writer(perm: Permission) -> Callable[[Optional[Role]], bool]:
    return lambda role: role in WRITE_ROLES and has_permission(role, perm)


class AIAgent:
    def __init__(self, api_key: str, model: str, tz, warehouse=None, flows=None, shop=None, attendance=None,
                 pending=None, client: Any = None) -> None:
        self.model = model
        self._tz = tz
        self._wh, self._flows, self._shop, self._att, self._pending = warehouse, flows, shop, attendance, pending
        self._client = client
        if self._client is None and api_key:
            from google import genai                       # faqat kalit bo'lsa yuklanadi
            self._client = genai.Client(api_key=api_key)
        self._sem = asyncio.Semaphore(2)
        self._last: Dict[int, float] = {}
        self._history: Dict[int, Tuple[float, Deque[Any]]] = {}
        self.drafts = DraftStore()
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
        product = {"type": S, "description": "Mahsulot nomi (taxminiy bo'lishi mumkin)"}
        day = {"type": S, "description": "Sana YYYY-MM-DD (kiritilmasa — bugun)"}
        read_wh = _perm(Permission.VIEW_DEFICIT)
        return [
            # ---- o'qish ----
            Tool("search_products", "Ombordagi mahsulotni nomi bo'yicha qidirish: qoldiq, birlik, kategoriya, "
                 "Мин. остаток, tanqisligi, o'rtacha narx.", {"query": {"type": S, "description": "Nom yoki qismi"}},
                 ["query"], read_wh, self._t_search),
            Tool("low_stock_products", "Tanqis mahsulotlar (qoldiq ≤ Мин. остаток).", {}, [], read_wh, self._t_low),
            Tool("products_in_category", "Kategoriya mahsulotlari va qoldiqlari; kategoriya bo'sh bo'lsa — "
                 "kategoriyalar ro'yxati.", {"category": {"type": S, "description": "Kategoriya"}}, [], read_wh,
                 self._t_category),
            Tool("stock_value", "Ombordagi jami tovar qiymati va kategoriyalar bo'yicha qiymat (so'm).", {}, [],
                 _staff, self._t_value),
            Tool("unpriced_inflows", "«Приход» dagi narxi kiritilmagan kirimlar.", {}, [], _staff, self._t_unpriced),
            Tool("warehouse_movements", "Davr bo'yicha kirim (Приход) va chiqim (Расход): soni, summasi, eng katta "
                 "mahsulotlar; bugungi qatorlar ham.", period, ["start_date"], _staff, self._t_flows),
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
            # ---- yozish: faqat qoralama, foydalanuvchi ✅ bosadi ----
            Tool("draft_inflow", "📥 Kirim (Приход) qoralamasi. Miqdor dona/birlikda yoki karobkada "
                 "(unit_mode=box). Narx noma'lum bo'lsa — 0 (narxi keyin kiritiladi).",
                 {"product": product, "quantity": {"type": N, "description": "Miqdor"},
                  "unit_mode": {"type": S, "description": "unit (dona/birlik) yoki box (karobka)"},
                  "price_per_unit": {"type": N, "description": "1 birlik narxi, so'm (0 — keyin)"},
                  "price_per_box": {"type": N, "description": "1 karobka narxi, so'm (ixtiyoriy)"},
                  "supplier": {"type": S, "description": "Do'kon / yetkazib beruvchi"}},
                 ["product", "quantity", "supplier"], _writer(Permission.MANAGE_WAREHOUSE), self._w_inflow, True),
            Tool("draft_outflow", "📤 Chiqim (Расход) qoralamasi: qoldiq tekshiriladi.",
                 {"product": product, "quantity": {"type": N, "description": "Miqdor (birlikda)"},
                  "recipient": {"type": S, "description": "Oluvchi usta (Ответственный)"},
                  "purpose": {"type": S, "description": "Buyurtma / sex / izoh (ixtiyoriy)"}},
                 ["product", "quantity", "recipient"], _writer(Permission.MANAGE_WAREHOUSE), self._w_outflow, True),
            Tool("draft_new_product", "➕ Yangi mahsulot qoralamasi. Kategoriya standartlardan (Фурнитура, "
                 "Крепеж / Метизы, Клей и Химия, Поролон и Обивка, Расходники, Инструменты, Кромка ПВХ); "
                 "Мин. остаток > 0 majburiy.",
                 {"name": {"type": S, "description": "Mahsulot nomi"},
                  "category": {"type": S, "description": "Kategoriya (bo'sh — avtomatik)"},
                  "unit": {"type": S, "description": "O'lchov birligi (шт, кг, пара, рулон, пачка ...)"},
                  "min_stock": {"type": N, "description": "Мин. остаток (> 0)"},
                  "pack": {"type": N, "description": "1 karobkadagi dona (ixtiyoriy, standart 1)"}},
                 ["name", "unit", "min_stock"], _writer(Permission.MANAGE_WAREHOUSE), self._w_product, True),
            Tool("draft_stock_adjustment", "⚖️ Qoldiqni to'g'rilash qoralamasi: +N qo'shadi, -N ayiradi.",
                 {"product": product, "delta": {"type": N, "description": "+N yoki -N"}},
                 ["product", "delta"], _writer(Permission.MANAGE_WAREHOUSE), self._w_adjust, True),
            Tool("draft_set_price", "💰 Narxi kiritilmagan kirimga narx belgilash qoralamasi.",
                 {"product": product, "price_per_unit": {"type": N, "description": "1 birlik narxi, so'm"},
                  "date": {"type": S, "description": "Kirim sanasi YYYY-MM-DD (bir nechta bo'lsa)"}},
                 ["product", "price_per_unit"], _writer(Permission.MANAGE_WAREHOUSE), self._w_price, True),
            Tool("draft_shop_expense", "🏪 Kunlik do'kon xarajati qoralamasi (kun summasi yangilanadi). Qisqa yozuv: "
                 "450 = 450 000 so'm.", {"amount": {"type": S, "description": "Summa, foydalanuvchi yozganidek"},
                                        "date": day},
                 ["amount"], _writer(Permission.MARK_EXPENSES), self._w_shop, True),
            Tool("draft_other_expense", "🧾 Boshqa xarajat (Прочие расходы) qoralamasi. Kategoriyalar: Топливо / "
                 "Бензин, Офис / Помещение, Транспорт / Грузы, Ремонт, Прочее. Qisqa yozuv: 450 = 450 000.",
                 {"amount": {"type": S, "description": "Summa, foydalanuvchi yozganidek"},
                  "category": {"type": S, "description": "Kategoriya"},
                  "description": {"type": S, "description": "Nima uchun"}, "date": day},
                 ["amount", "description"], _writer(Permission.MANAGE_WAREHOUSE), self._w_other, True),
        ]

    def tools_for(self, role: Optional[Role]) -> List[Tool]:
        return [t for t in self._tools.values() if t.allow(role)]

    def _period(self, args: Dict[str, Any]):
        today = self.today()
        end = _parse_day(args.get("end_date"), today)
        start = _parse_day(args.get("start_date"), end)
        if start > end:
            start, end = end, start
        if (end - start).days > MAX_RANGE_DAYS:
            start = end - timedelta(days=MAX_RANGE_DAYS)
        return start, end

    @staticmethod
    def _product_row(p) -> Dict[str, Any]:
        return {"name": p.name, "category": p.category, "unit": p.unit, "stock": _r(p.stock),
                "min_norm": _r(p.min_stock), "low": bool(p.is_low), "pack": _r(p.pack),
                "avg_price_sum": _r(p.avg_price), "stock_value_sum": _r(p.value or p.stock * p.avg_price)}

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

    # ---- o'qish ----
    async def _t_search(self, ctx: Ctx, query: str = "") -> Any:
        catalog = await self._wh.catalog()
        names = [p.name for p in catalog]
        q = normalize(query).replace(" ", "")
        hits = [n for n in names if q and q in normalize(n).replace(" ", "")][:10] or search_products(query, names, 5)
        by = {p.name: p for p in catalog}
        return {"matches": [self._product_row(by[n]) for n in hits if n in by]}

    async def _t_low(self, ctx: Ctx) -> Any:
        low = sorted(await self._wh.low_items(), key=lambda p: p.stock / (p.min_stock or 1))
        return {"count": len(low), "items": [self._product_row(p) for p in low[:40]]}

    async def _t_category(self, ctx: Ctx, category: str = "") -> Any:
        catalog = await self._wh.catalog()
        if not category:
            counts: Dict[str, int] = {}
            for p in catalog:
                counts[p.category or "—"] = counts.get(p.category or "—", 0) + 1
            return {"categories": counts, "products_total": len(catalog)}
        k = normalize(category)
        items = [p for p in catalog if k and k in normalize(p.category or "")]
        return {"category": category, "count": len(items), "items": [self._product_row(p) for p in items[:60]]}

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
            price = box_price / (p.pack if box or p.pack > 1 else 1)
        price = price if price and price > 0 else None
        supplier = _clean(supplier, 60)
        if not supplier:
            from app.services.warehouse import SUPPLIER
            return {"error": "supplier required", "known_suppliers": await self._wh.top_names(SUPPLIER, 8)}
        boxes = f" ({_n(q)} {t['wh_unit_box']} × {_n(p.pack)})" if box else ""
        card = t["ai_d_inflow"].format(
            name=html.escape(p.name), qty=_n(qty), unit=html.escape(p.unit), boxes=boxes,
            price=(f"{_n(price)} so'm · jami {_n(price * qty)}" if ctx.lang != "ru" else
                   f"{_n(price)} сум · итого {_n(price * qty)}") if price else t["wh_price_later_mark"],
            supplier=html.escape(supplier))
        return self._draft(ctx, "inflow", {"name": p.name, "unit": p.unit, "qty": qty, "price": price,
                                           "supplier": supplier, "boxes": q if box else 0.0, "pack": p.pack}, card)

    async def _w_outflow(self, ctx: Ctx, product: str = "", quantity: Any = None, recipient: str = "",
                         purpose: str = "") -> Any:
        t = get_texts(ctx.lang)
        p, err = await self._need_product(product)
        if err:
            return err
        qty = _num(quantity)
        if not qty or qty <= 0:
            return {"error": "quantity must be > 0"}
        if qty > p.stock + 1e-9:
            return {"error": "insufficient_stock", "stock": _r(p.stock), "unit": p.unit}
        recipient = _clean(recipient, 60)
        if not recipient:
            from app.services.warehouse import RECIPIENT
            return {"error": "recipient required", "known_recipients": await self._wh.top_names(RECIPIENT, 8)}
        purpose = _clean(purpose, 100)
        card = t["ai_d_outflow"].format(name=html.escape(p.name), qty=_n(qty), unit=html.escape(p.unit),
                                        stock=_n(p.stock), after=_n(p.stock - qty), who=html.escape(recipient),
                                        purpose=html.escape(purpose) or "—")
        return self._draft(ctx, "outflow", {"name": p.name, "unit": p.unit, "qty": qty, "recipient": recipient,
                                            "purpose": purpose}, card)

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
        return self._draft(ctx, "adjust", {"name": p.name, "unit": p.unit, "delta": d,
                                           "price": p.avg_price or None}, card)

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

    async def _w_shop(self, ctx: Ctx, amount: Any = None, date: str = "") -> Any:
        t = get_texts(ctx.lang)
        value = _money(amount)
        if value is None or value <= 0:
            return {"error": "amount not understood"}
        today = self.today()
        day = _parse_day(date, today)
        if day > today:
            return {"error": "future date"}
        if self._pending is not None and await self._pending.is_sent("shop", day.isoformat()) is True:
            return {"error": "already_sent_to_boss",
                    "hint": "Changes after sending need a reason: use the 🏪 Do'kon xarajati button."}
        found = await self._shop.get_day(day)
        old = found[1] if found and found[0] and found[1] is not None else None
        card = t["ai_d_shop"].format(date=day.strftime("%d.%m.%Y"), old=_n(old) if old else "—", amount=_n(value))
        return self._draft(ctx, "shop", {"iso": day.isoformat(), "amount": value}, card)

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
        refusal = get_texts(lang)["ai_offtopic"]
        language = "Russian" if lang == "ru" else "Uzbek (Latin script)"
        if role in WRITE_ROLES:
            rights = ("This user MAY record operations, but ONLY by calling the draft_* tools. A draft is NOT saved: "
                      "tell the user to check the card and press ✅ Tasdiqlash. Never claim something is saved. "
                      "If a tool returns an error (missing supplier/recipient, ambiguous product, not enough stock), "
                      "ask ONE short question. If the product is missing, offer to create it with draft_new_product.")
        else:
            rights = ("This user is the Boss: READ-ONLY analytics. If asked to record an inflow, outflow, price, "
                      "product or expense, politely say that only the Admin and the Storekeeper record operations.")
        return (
            "You are the assistant of the «Ziyo Zamir MCHJ» furniture factory Telegram bot. "
            f"Today: {today.isoformat()} ({today.strftime('%A')}), timezone Asia/Tashkent. "
            f"User role: {role.value if role else 'unknown'}. Reply in {language}; if the user writes in another "
            "language, reply in that language.\n"
            "DOMAIN LOCK: answer ONLY about this factory's work: warehouse stock, inflows/outflows (Приход/Расход), "
            "product catalog and prices, store expenses (Do'kon xarajati), other operational expenses (Прочие "
            "расходы), staff attendance and payroll, and how to use this bot. For ANYTHING else (general chat, "
            "weather, news, jokes, coding, recipes, personal advice, politics, translations, math homework, etc.) "
            f"do not answer it and reply with exactly this one sentence and nothing more: {refusal}\n"
            "Get every number ONLY from the tools; never invent data; if not found, say so. Money in so'm with "
            "space thousands (1 250 000). Resolve 'this week', 'last month' from today. Be brief: at most 6 short "
            "lines or bullets, plain text, **bold** allowed. Bot buttons for manual work: 📥 Kirim, 📤 Chiqim, "
            "📦 Mahsulotlar, 📊 Qoldiqlar, 🧾 Boshqa xarajatlar, 📝 Yo'qlama, 🏪 Do'kon xarajati.\n" + rights)

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

    def _config(self, role: Optional[Role], lang: str):
        from google.genai import types
        kwargs: Dict[str, Any] = dict(
            system_instruction=self._system(role, lang), tools=self._declarations(role),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        if re.match(r"^gemini-[12]\.", self.model or ""):
            kwargs["temperature"] = TEMPERATURE            # Gemini 3+: temperature eskirgan — yuborilmaydi
        return types.GenerateContentConfig(**kwargs)

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
                  image_mime: str = "image/jpeg", name: str = "") -> "AgentReply":
        """Javob (Telegram HTML) + tasdiqlash kerak bo'lgan qoralamalar."""
        if not self.enabled:
            raise RuntimeError("AI agent is disabled")
        if not ai_allowed(role):
            raise AgentDenied()
        from google.genai import errors, types
        self._check_cooldown(user_id)
        ctx = Ctx(user_id, role, lang, name)
        parts = []
        if audio:
            parts.append(types.Part.from_bytes(data=audio, mime_type="audio/ogg"))
        if image:
            parts.append(types.Part.from_bytes(data=image, mime_type=image_mime))
        prompt = (text or "").strip() or ("Listen to the voice message and answer it." if audio else
                                          "What is in this photo (work-related)?" if image else "")
        parts.append(types.Part(text=prompt[:2000]))
        history = self._history_for(user_id)
        contents: List[Any] = list(history) + [types.Content(role="user", parts=parts)]
        config = self._config(role, lang)
        answer = ""
        async with self._sem:
            try:
                for _ in range(MAX_ROUNDS):
                    resp = await self._client.aio.models.generate_content(model=self.model, contents=contents,
                                                                          config=config)
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
            except errors.APIError as e:
                if getattr(e, "code", None) in (429, 503):
                    raise AgentBusy() from e
                raise
        if not answer:
            answer = get_texts(lang)["ai_draft_hint"] if ctx.drafts else "…"
        note = (text or "").strip() or ("[voice]" if audio else "[photo]")
        history.append(types.Content(role="user", parts=[types.Part(text=note[:500])]))
        history.append(types.Content(role="model", parts=[types.Part(text=answer[:800])]))
        return AgentReply(to_telegram_html(answer), ctx.drafts)


@dataclass
class AgentReply:
    text: str
    drafts: List[Draft] = field(default_factory=list)
