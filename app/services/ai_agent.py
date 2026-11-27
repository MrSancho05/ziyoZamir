"""🤖 AI yordamchi: Google Gemini (bepul tarif, `gemini-2.5-flash-lite`) + botning o'z ma'lumotlari (tool calling).

Matn, ovozli xabar (audio/ogg) va rasm (image/jpeg) qabul qiladi. Raqamlar FAQAT botning funksiyalari
(Google Sheets / SQLite) orqali olinadi — model o'zidan raqam to'qimasligi kerak.

Xavfsizlik:
- barcha vositalar faqat O'QIYDI: AI jadvalga hech narsa yozmaydi, o'chirmaydi;
- vositalar rolga qarab beriladi: ishchi (worker) — faqat qoldiq (narx va pullarsiz);
- bitta foydalanuvchi uchun qisqa kutish (bepul tarif cheklovi) va bir vaqtda ko'pi bilan 2 ta so'rov.
Python 3.9+ bilan mos (google-genai>=1.0.0)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Sequence

from app.core.permissions import Role
from app.services.search import normalize, search_products

logger = logging.getLogger(__name__)

MAX_ROUNDS = 5               # bitta savolda vosita chaqiruvlari soni
COOLDOWN = 3.0               # soniya: bitta foydalanuvchi so'rovlari orasida
HISTORY_TURNS = 6            # qisqa suhbat xotirasi (savol + javob juftlari emas, alohida xabarlar)
MAX_ANSWER = 3800            # Telegram xabari 4096 belgidan oshmasin
MAX_RANGE_DAYS = 366

STAFF = (Role.ADMIN, Role.BOSS, Role.STOREKEEPER)
ALL_ROLES = STAFF + (Role.WORKER,)


class AgentBusy(Exception):
    """Bepul tarif limiti (429) yoki band."""


class AgentCooldown(Exception):
    pass


@dataclass
class Tool:
    name: str
    description: str
    params: Dict[str, Dict[str, str]]          # nom -> {"type": "string", "description": ...}
    required: Sequence[str]
    roles: Sequence[Role]
    run: Callable[..., Awaitable[Any]]


def _r(x: Any) -> Any:
    try:
        v = round(float(x or 0), 2)
    except (TypeError, ValueError):
        return x
    return int(v) if v == int(v) else v


def _parse_day(value: Any, default: date) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def to_telegram_html(text: str) -> str:
    """Modelning markdown javobi -> xavfsiz Telegram HTML (faqat <b>, <i>, <code>)."""
    s = html.escape(str(text or "").strip(), quote=False)
    s = re.sub(r"^\s{0,3}#{1,6}\s*(.+)$", r"<b>\1</b>", s, flags=re.M)        # sarlavhalar
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"^(\s*)[*\-]\s+", r"\1• ", s, flags=re.M)                     # ro'yxat belgilari
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = s.replace("```", "")
    if len(s) > MAX_ANSWER:
        s = s[:MAX_ANSWER].rsplit("\n", 1)[0] + "\n…"
    return s


class AIAgent:
    def __init__(self, api_key: str, model: str, tz, warehouse=None, flows=None, shop=None, attendance=None,
                 client: Any = None) -> None:
        self.model = model
        self._tz = tz
        self._wh, self._flows, self._shop, self._att = warehouse, flows, shop, attendance
        self._client = client
        if self._client is None and api_key:
            from google import genai                       # faqat kalit bo'lsa yuklanadi
            self._client = genai.Client(api_key=api_key)
        self._sem = asyncio.Semaphore(2)
        self._last: Dict[int, float] = {}
        self._history: Dict[int, Deque[Any]] = {}
        self._tools: Dict[str, Tool] = {t.name: t for t in self._build_tools()}

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def today(self) -> date:
        return datetime.now(self._tz).date()

    def forget(self, user_id: int) -> None:
        self._history.pop(user_id, None)

    # ---------------- vositalar (faqat o'qish) ----------------
    def _build_tools(self) -> List[Tool]:
        S = "string"
        period = {"start_date": {"type": S, "description": "Boshlanish sanasi, YYYY-MM-DD"},
                  "end_date": {"type": S, "description": "Tugash sanasi, YYYY-MM-DD (kiritilmasa — bugun)"}}
        return [
            Tool("search_products", "Ombordagi mahsulotni nomi bo'yicha qidirish (xato yozilgan nom ham): qoldiq, "
                 "birlik, kategoriya, Мин. норматив, tanqisligi va (ruxsat bo'lsa) o'rtacha narxi.",
                 {"query": {"type": S, "description": "Mahsulot nomi yoki uning qismi"}}, ["query"], ALL_ROLES,
                 self._t_search),
            Tool("low_stock_products", "Tanqis mahsulotlar: qoldig'i Мин. норматив ga teng yoki undan kam.",
                 {}, [], ALL_ROLES, self._t_low),
            Tool("products_in_category", "Kategoriya bo'yicha mahsulotlar ro'yxati va qoldiqlari. Kategoriya "
                 "kiritilmasa — barcha kategoriyalar va ulardagi mahsulot soni.",
                 {"category": {"type": S, "description": "Kategoriya nomi (masalan: Фурнитура)"}}, [], ALL_ROLES,
                 self._t_category),
            Tool("stock_value", "Ombordagi jami tovar qiymati (so'm) va kategoriyalar bo'yicha qiymati.",
                 {}, [], STAFF, self._t_value),
            Tool("unpriced_inflows", "«Приход» dagi narxi kiritilmagan kirimlar (sana, mahsulot, miqdor, do'kon).",
                 {}, [], STAFF, self._t_unpriced),
            Tool("warehouse_movements", "Davr bo'yicha ombor kirimi (Приход) va chiqimi (Расход): soni, summasi, "
                 "eng katta mahsulotlar. Bugungi qatorlar ham hisobga olinadi.",
                 period, ["start_date"], STAFF, self._t_flows),
            Tool("shop_expenses", "Do'kon xarajati (Магазин varag'i) davr bo'yicha: jami, kunlar bo'yicha, "
                 "summa kiritilmagan kunlar.", period, ["start_date"], STAFF, self._t_shop),
            Tool("other_expenses", "🧾 Boshqa xarajatlar (Прочие расходы) davr bo'yicha: jami va kategoriyalar.",
                 period, ["start_date"], STAFF, self._t_other),
            Tool("attendance_summary", "Davomat (yo'qlama) davr bo'yicha: har bir ishchi necha kun keldi, "
                 "sababsiz / sababli kelmadi.", period, ["start_date"], STAFF, self._t_att),
        ]

    def tools_for(self, role: Optional[Role]) -> List[Tool]:
        return [t for t in self._tools.values() if role in t.roles]

    @staticmethod
    def _prices(role: Optional[Role]) -> bool:
        return role in STAFF

    def _period(self, args: Dict[str, Any]):
        today = self.today()
        end = _parse_day(args.get("end_date"), today)
        start = _parse_day(args.get("start_date"), end)
        if start > end:
            start, end = end, start
        if (end - start).days > MAX_RANGE_DAYS:
            start = end - timedelta(days=MAX_RANGE_DAYS)
        return start, end

    def _product_row(self, p, role) -> Dict[str, Any]:
        row = {"name": p.name, "category": p.category, "unit": p.unit, "stock": _r(p.stock),
               "min_norm": _r(p.min_stock), "low": bool(p.is_low)}
        if self._prices(role):
            row["avg_price_sum"] = _r(p.avg_price)
            row["stock_value_sum"] = _r(p.value or p.stock * p.avg_price)
        return row

    async def _t_search(self, role, query: str = "") -> Any:
        catalog = await self._wh.catalog()
        names = [p.name for p in catalog]
        q = normalize(query).replace(" ", "")
        hits = [n for n in names if q and q in normalize(n).replace(" ", "")][:10] or search_products(query, names, 5)
        by = {p.name: p for p in catalog}
        return {"matches": [self._product_row(by[n], role) for n in hits if n in by]}

    async def _t_low(self, role) -> Any:
        low = sorted(await self._wh.low_items(), key=lambda p: p.stock / (p.min_stock or 1))
        return {"count": len(low), "items": [self._product_row(p, role) for p in low[:40]]}

    async def _t_category(self, role, category: str = "") -> Any:
        catalog = await self._wh.catalog()
        if not category:
            counts: Dict[str, int] = {}
            for p in catalog:
                counts[p.category or "—"] = counts.get(p.category or "—", 0) + 1
            return {"categories": counts, "products_total": len(catalog)}
        k = normalize(category)
        items = [p for p in catalog if k and k in normalize(p.category or "")]
        return {"category": category, "count": len(items), "items": [self._product_row(p, role) for p in items[:60]]}

    async def _t_value(self, role) -> Any:
        catalog = await self._wh.catalog()
        by: Dict[str, float] = {}
        for p in catalog:
            by[p.category or "—"] = by.get(p.category or "—", 0.0) + (p.value or p.stock * p.avg_price)
        return {"total_sum": _r(sum(by.values())), "by_category_sum": {k: _r(v) for k, v in by.items()}}

    async def _t_unpriced(self, role) -> Any:
        rows = await self._wh.unpriced()
        return {"count": len(rows), "items": [{"date": r.day.isoformat() if r.day else "", "name": r.name,
                                               "qty": _r(r.qty), "unit": r.unit, "supplier": r.supplier}
                                              for r in rows[:40]]}

    async def _t_flows(self, role, **args) -> Any:
        start, end = self._period(args)
        summary = await self._flows.summary(start, end) if self._flows else {}
        out = {"period": [start.isoformat(), end.isoformat()]}
        for key, label in (("inflow", "prixod"), ("outflow", "rasxod")):
            s = summary.get(key)
            out[label] = {"rows": s.count if s else 0, "sum": _r(s.amount) if s else 0,
                          "top": [{"name": n, "qty": _r(q), "sum": _r(a)} for n, q, a in (s.top if s else [])]}
        today = self.today()
        if self._flows and start <= today <= end:          # bugungi qatorlar kunlik yopilishdan oldin ham
            try:
                deltas = await self._flows.preview(today)            # faqat o'qiydi
                stored_today = await self._flows.summary(today, today)
                for key, label in (("inflow", "prixod"), ("outflow", "rasxod")):
                    d = deltas.get(key)
                    already = stored_today.get(key)
                    if d and d.new_rows and not (already and already.count):   # 18:30 dan keyin — takrorlanmaydi
                        out[label]["rows"] += len(d.new_rows)
                        out[label]["sum"] = _r(out[label]["sum"] + sum(r.amount or 0 for r in d.new_rows))
            except Exception:
                logger.warning("AI: bugungi kirim/chiqimni o'qib bo'lmadi", exc_info=True)
        return out

    async def _t_shop(self, role, **args) -> Any:
        start, end = self._period(args)
        st = await self._shop.period_stats(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "total_sum": _r(st.total),
                "per_day_sum": {d.isoformat(): _r(v) for d, v in sorted(st.per_day.items())},
                "days_without_amount": [d.isoformat() for d in st.missing][:31]}

    async def _t_other(self, role, **args) -> Any:
        other = getattr(self._wh, "other", None)
        if other is None:
            return {"error": "not configured"}
        start, end = self._period(args)
        s = await other.summary(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "total_sum": _r(s.total), "count": s.count,
                "by_category_sum": {k: _r(v) for k, v in s.by_category.items()},
                "rows": [{"date": r.day.isoformat(), "category": r.category, "description": r.description,
                          "sum": _r(r.amount)} for r in s.rows[:30]]}

    async def _t_att(self, role, **args) -> Any:
        start, end = self._period(args)
        st = await self._att.period_stats(start, end)
        return {"period": [start.isoformat(), end.isoformat()], "recorded_days": st.recorded_days,
                "work_days": st.work_days,
                "workers": [{"name": w.name, "came": w.came, "absent": w.absent, "excused": w.excused}
                            for w in st.workers]}

    async def run_tool(self, name: str, args: Dict[str, Any], role: Optional[Role]) -> Any:
        tool = self._tools.get(name)
        if tool is None or role not in tool.roles:
            return {"error": "tool not allowed"}
        allowed = {k: v for k, v in (args or {}).items() if k in tool.params}
        try:
            result = await tool.run(role, **allowed)
        except Exception as e:                               # vosita xatosi modelga tushunarli qilib qaytadi
            logger.exception("AI vositasi xatosi: %s", name)
            return {"error": type(e).__name__}
        text = json.dumps(result, ensure_ascii=False, default=str)
        return result if len(text) < 12000 else {"truncated": text[:12000]}

    # ---------------- Gemini ----------------
    def _system(self, role: Optional[Role], lang: str) -> str:
        today = self.today()
        language = "rus tilida" if lang == "ru" else "o'zbek tilida (lotin yozuvi)"
        money = "" if role in STAFF else (" Bu foydalanuvchi ishchi: narx, summa va pul ma'lumotlarini "
                                          "aytma — faqat qoldiq va tanqislik.")
        return (
            "Sen «Ziyo Zamir MCHJ» mebel fabrikasining Telegram botidagi yordamchisan. "
            f"Bugun: {today.isoformat()} ({today.strftime('%A')}), vaqt zonasi Asia/Tashkent. "
            f"Foydalanuvchi roli: {role.value if role else 'unknown'}. "
            f"Javobni {language} yoz, foydalanuvchi boshqa tilda yozsa — o'sha tilda. "
            "Qisqa, aniq va amaliy yoz; Telegram uchun oddiy matn, kerak bo'lsa **qalin** va ro'yxat. "
            "Ombor, xarajat va davomat raqamlarini FAQAT vositalar (functions) orqali ol, hech qachon o'zingdan "
            "raqam to'qima; ma'lumot topilmasa — shuni ochiq ayt. Pullar so'mda, mingliklarni bo'sh joy bilan yoz "
            "(1 250 000). «Bu hafta», «o'tgan oy» kabi davrlarni bugungi sanadan hisobla. "
            "Sen jadvalga hech narsa yoza olmaysan: kirim/chiqim, narx, yangi mahsulot uchun botning tugmalariga "
            "yo'naltir — 📥 Kirim, 📤 Chiqim, 📦 Mahsulotlar (✏️ tahrirlash, 💰 narx, ⚖️ qoldiqni to'g'rilash), "
            "📊 Qoldiqlar, 🧾 Boshqa xarajatlar, 📝 Yo'qlama, 🏪 Do'kon xarajati. "
            "Ovozli xabarni tinglab, unga javob ber; rasmda mahsulot yoki hujjat (chek, nakladnoy) bo'lsa — "
            "nimaligini ayt va kerak bo'lsa ombordan qidir." + money
        )

    def _declarations(self, role: Optional[Role]):
        from google.genai import types
        decls = []
        for tool in self.tools_for(role):
            params = None
            if tool.params:
                params = types.Schema(
                    type=types.Type.OBJECT,
                    properties={k: types.Schema(type=types.Type.STRING, description=v.get("description", ""))
                                for k, v in tool.params.items()},
                    required=list(tool.required) or None)
            decls.append(types.FunctionDeclaration(name=tool.name, description=tool.description, parameters=params))
        return [types.Tool(function_declarations=decls)] if decls else None

    def _check_cooldown(self, user_id: int) -> None:
        now = time.monotonic()
        if now - self._last.get(user_id, 0.0) < COOLDOWN:
            raise AgentCooldown()
        self._last[user_id] = now

    async def ask(self, user_id: int, role: Optional[Role], lang: str, text: str = "",
                  audio: Optional[bytes] = None, image: Optional[bytes] = None,
                  image_mime: str = "image/jpeg") -> str:
        """Javob (Telegram HTML). Xatolar: AgentCooldown, AgentBusy; boshqa xatolar — tashqariga."""
        if not self.enabled:
            raise RuntimeError("AI agent is disabled")
        from google.genai import errors, types
        self._check_cooldown(user_id)
        parts = []
        if audio:
            parts.append(types.Part.from_bytes(data=audio, mime_type="audio/ogg"))
        if image:
            parts.append(types.Part.from_bytes(data=image, mime_type=image_mime))
        prompt = text.strip() if text else ""
        if not prompt:
            prompt = ("Ovozli xabarni tinglab, unga javob ber." if audio else
                      "Rasmda nima borligini ayt va kerak bo'lsa ombordan qidir." if image else "")
        parts.append(types.Part(text=prompt[:4000]))
        history = self._history.setdefault(user_id, deque(maxlen=HISTORY_TURNS))
        contents: List[Any] = list(history) + [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            system_instruction=self._system(role, lang), tools=self._declarations(role), temperature=0.3,
            max_output_tokens=1024,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
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
                    contents.append(resp.candidates[0].content)
                    replies = []
                    for call in calls:
                        logger.info("AI vositasi: %s (user=%s)", call.name, user_id)
                        result = await self.run_tool(call.name, dict(call.args or {}), role)
                        replies.append(types.Part.from_function_response(name=call.name, response={"result": result}))
                    contents.append(types.Content(role="user", parts=replies))
            except errors.APIError as e:
                if getattr(e, "code", None) in (429, 503):
                    raise AgentBusy() from e
                raise
        if not answer:
            answer = "…"
        # xotira: faqat matn (audio / rasm takror yuborilmaydi)
        note = text.strip() or ("[ovozli xabar]" if audio else "[rasm]")
        history.append(types.Content(role="user", parts=[types.Part(text=note[:1000])]))
        history.append(types.Content(role="model", parts=[types.Part(text=answer[:2000])]))
        return to_telegram_html(answer)
