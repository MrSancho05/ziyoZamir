"""📊 Jonli Dashboard — Telegram Mini App uchun kichik aiohttp server (bot polling bilan bir jarayonda).

Marshrutlar:
    GET /, GET /dashboard     -> app/webapp/dashboard.html (ma'lumotsiz statik sahifa)
    GET /api/dashboard        -> KPI, grafiklar, tanqis tovarlar (JSON)
    GET /healthz              -> "ok"

Xavfsizlik: /api/dashboard faqat Telegram imzolagan `initData` bilan ishlaydi (X-Telegram-Init-Data sarlavhasi,
bot tokeni bilan HMAC-SHA256 tekshiriladi) va faqat bazadagi faol BOSS yoki ADMIN uchun. Sahifaning o'zida
hech qanday maxfiy ma'lumot yo'q.

Tezlik / kvota: har bir (davr, mavsum, siljish) uchun xom ma'lumot TTL keshda saqlanadi (standart 60 s) va bir
vaqtdagi bir xil so'rovlar bitta o'qishga birlashtiriladi. Til faqat yorliqlarni o'zgartiradi, ma'lumot qayta
o'qilmaydi — qayta bosishlar Google Sheets ga umuman murojaat qilmaydi.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

from aiohttp import web

from app.core.permissions import Role
from app.locales.texts import get_texts, month_name, weekday_short
from app.services.categories import label as cat_label
from app.services.inspect_reports import period_label
from app.utils.periods import SEASONS, TIMEFRAMES, TF_SEASONAL, TF_YEARLY, WINTER, Range, shifted_range

logger = logging.getLogger(__name__)

HTML_PATH = Path(__file__).resolve().parent.parent / "webapp" / "dashboard.html"
CACHE_TTL = 60.0
INIT_DATA_MAX_AGE = 24 * 3600
MAX_BACK = 60                       # orqaga eng ko'p 60 davr
ALLOWED_ROLES = (Role.BOSS, Role.ADMIN)


# ---------------- Telegram initData tekshiruvi ----------------
def verify_init_data(init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE,
                     now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Telegram WebApp initData imzosini tekshiradi. To'g'ri bo'lsa `user` obyektini, aks holda None qaytaradi.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    if max_age and (now or time.time()) - auth_date > max_age:
        return None
    return user if isinstance(user, dict) and user.get("id") else None


# ---------------- ma'lumot yig'ish ----------------
@dataclass
class RawData:
    rng: Range
    shop_days: Dict[date, int] = field(default_factory=dict)
    shop_total: Optional[int] = None
    shop_prev: Optional[int] = None
    out_rows: List[Tuple[date, str, float, float]] = field(default_factory=list)   # (kun, tovar, miqdor, qiymat)
    out_prev: float = 0.0
    in_total: float = 0.0
    stock_value: Optional[float] = None
    low: List[Dict[str, Any]] = field(default_factory=list)
    units: Dict[str, str] = field(default_factory=dict)
    cats: Dict[str, str] = field(default_factory=dict)          # tovar (casefold) -> kategoriya
    att: Optional[Dict[str, int]] = None
    errors: List[str] = field(default_factory=list)
    built_at: float = 0.0


def _n(x: float) -> float:
    return int(x) if float(x).is_integer() else round(x, 2)


class DashboardService:
    def __init__(self, shop, flows, attendance, warehouse, tz, ttl: float = CACHE_TTL) -> None:
        self._shop, self._flows, self._att, self._wh = shop, flows, attendance, warehouse
        self._tz = tz
        self._ttl = ttl
        self._cache: Dict[Tuple, RawData] = {}
        self._locks: Dict[Tuple, asyncio.Lock] = {}

    def today(self) -> date:
        return datetime.now(self._tz).date()

    async def raw(self, tf: str, season: int, offset: int) -> RawData:
        key = (self.today(), tf, season if tf == TF_SEASONAL else 0, offset)
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit.built_at < self._ttl:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:                                   # bir vaqtdagi bir xil so'rovlar — bitta o'qish
            hit = self._cache.get(key)
            if hit is not None and time.monotonic() - hit.built_at < self._ttl:
                return hit
            data = await self._build(shifted_range(tf, self.today(), season, offset))
            data.built_at = time.monotonic()
            if len(self._cache) > 200:
                self._cache.clear()
            self._cache[key] = data
            return data

    async def _build(self, rng: Range) -> RawData:
        d = RawData(rng=rng)
        catalog = []
        try:
            catalog = await self._wh.catalog()                          # 60 s katalog keshi
            d.stock_value = await self._wh.stock_value(fresh=False)
            d.units = {p.name.casefold(): p.unit for p in catalog}
            d.cats = {p.name.casefold(): p.category for p in catalog}
            d.low = [{"name": p.name, "stock": _n(p.stock), "min": _n(p.min_stock), "unit": p.unit,
                      "pct": max(0, min(100, round(p.stock * 100 / p.min_stock))) if p.min_stock else 0}
                     for p in sorted((p for p in catalog if p.is_low), key=lambda p: p.stock / (p.min_stock or 1))]
        except Exception:
            logger.exception("Dashboard: katalogni o'qib bo'lmadi")
            d.errors.append("stock")
        avg = {p.name.casefold(): p.avg_price for p in catalog}

        try:
            cur = await self._shop.period_stats(rng.start, rng.end)
            prev = await self._shop.period_stats(rng.prev_start, rng.prev_end)
            d.shop_days, d.shop_total = dict(cur.per_day), cur.total
            d.shop_prev = prev.total if prev.per_day else None
        except Exception:
            logger.exception("Dashboard: do'kon xarajatini o'qib bo'lmadi")
            d.errors.append("shop")

        def value(r) -> float:            # Сумма расхода bo'lmasa: miqdor × o'rtacha narx
            if r.amount:
                return float(r.amount)
            return float(r.qty or 0) * avg.get(r.label.strip().casefold(), 0.0)

        try:
            for r in await self._flows.rows("outflow", rng.start, rng.end):
                d.out_rows.append((date.fromisoformat(r.day), r.label.strip() or "—", float(r.qty or 0), value(r)))
            d.out_prev = sum(value(r) for r in await self._flows.rows("outflow", rng.prev_start, rng.prev_end))
            d.in_total = sum(float(r.amount or 0) for r in await self._flows.rows("inflow", rng.start, rng.end))
        except Exception:
            logger.exception("Dashboard: kirim/chiqim tarixini o'qib bo'lmadi")
            d.errors.append("flows")

        try:
            if rng.start == rng.end and rng.start.weekday() == 6:
                d.att = None                                         # yakshanba
            else:
                st = await self._att.period_stats(rng.start, rng.end)
                came = sum(w.came for w in st.workers)
                excused = sum(w.excused for w in st.workers)
                absent = sum(w.absent for w in st.workers)
                total = came + excused + absent
                d.att = {"rate": round(came * 100 / total) if total else 0, "came": came, "excused": excused,
                         "absent": absent, "recorded": st.recorded_days, "work": st.work_days,
                         "workers": len(st.workers)}
        except Exception:
            logger.exception("Dashboard: davomatni o'qib bo'lmadi")
            d.errors.append("att")
        return d

    # ---------------- JSON (tilga qarab yorliqlar) ----------------
    def render(self, d: RawData, lang: str, offset: int) -> Dict[str, Any]:
        t = get_texts(lang)
        rng = d.rng
        label = re.sub(r"<[^>]+>", "", period_label(t, lang, rng))

        # vaqt qatori: d / w / m — kunlar, s / y — oylar
        by_month = rng.tf in (TF_SEASONAL, TF_YEARLY)
        buckets: Dict[Any, List[float]] = {}
        order: List[Any] = []
        day = rng.start
        while day <= rng.end:
            k = (day.year, day.month) if by_month else day
            if k not in buckets:
                buckets[k] = [0.0, 0.0]
                order.append(k)
            day += timedelta(days=1)
        for day, amount in d.shop_days.items():
            k = (day.year, day.month) if by_month else day
            if k in buckets:
                buckets[k][0] += amount
        for day, _name, _q, v in d.out_rows:
            k = (day.year, day.month) if by_month else day
            if k in buckets:
                buckets[k][1] += v
        if by_month:
            labels = [month_name(lang, m)[:3] for _y, m in order]
        else:
            labels = [f"{k.day:02d}.{k.month:02d} {weekday_short(lang, k.weekday())}" for k in order]

        # kategoriya va eng ko'p sarflangan tovarlar
        other = "Прочее" if lang == "ru" else "Boshqa"
        cats: Dict[str, float] = {}
        items: Dict[str, List[float]] = {}
        names: Dict[str, str] = {}
        for _day, name, qty, v in d.out_rows:
            key = name.casefold()
            cat = d.cats.get(key) or ""
            cats[cat_label(cat, lang) if cat else other] = cats.get(cat_label(cat, lang) if cat else other, 0.0) + v
            agg = items.setdefault(key, [0.0, 0.0])
            agg[0] += qty
            agg[1] += v
            names.setdefault(key, name)
        consumption = sum(v for *_x, v in d.out_rows)

        def delta(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
            if cur is None or not prev:
                return None
            return round((cur - prev) * 100 / prev, 1)

        top = sorted(items.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:7]
        return {
            "ok": True,
            "tf": rng.tf, "season": rng.season if rng.season is not None else WINTER, "offset": offset,
            "can_next": offset < 0, "can_prev": offset > -MAX_BACK,
            "label": label,
            "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat(),
                      "prev_start": rng.prev_start.isoformat(), "prev_end": rng.prev_end.isoformat()},
            "kpi": {
                "shop": {"value": d.shop_total, "prev": d.shop_prev, "delta": delta(d.shop_total, d.shop_prev)},
                "stock_value": round(d.stock_value) if d.stock_value is not None else None,
                "consumption": {"value": round(consumption), "prev": round(d.out_prev),
                                "delta": delta(consumption, d.out_prev)},
                "inflow": round(d.in_total),
                "attendance": d.att,
            },
            "series": {"labels": labels, "shop": [round(b[0]) for b in (buckets[k] for k in order)],
                       "outflow": [round(b[1]) for b in (buckets[k] for k in order)]},
            "categories": [{"name": k, "value": round(v)} for k, v in sorted(cats.items(), key=lambda kv: -kv[1])
                           if v > 0],
            "top": [{"name": names[k], "qty": _n(q), "unit": d.units.get(k, ""), "value": round(v)}
                    for k, (q, v) in top],
            "low": d.low[:30],
            "low_count": len(d.low),
            "errors": d.errors,
            "generated_at": datetime.now(self._tz).strftime("%H:%M"),
        }


# ---------------- aiohttp ilovasi ----------------
Authorizer = Callable[[web.Request], Awaitable[Optional[str]]]     # None — ruxsat; aks holda xato kodi


def make_authorizer(bot_token: str, repo) -> Authorizer:
    async def authorize(request: web.Request) -> Optional[str]:
        user = verify_init_data(request.headers.get("X-Telegram-Init-Data", ""), bot_token)
        if user is None:
            return "unauthorized"
        db_user = await repo.get(int(user["id"]))
        if db_user is None or not db_user.is_active or db_user.role not in ALLOWED_ROLES:
            return "forbidden"
        return None
    return authorize


def create_app(service: DashboardService, authorize: Authorizer) -> web.Application:
    async def page(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(HTML_PATH, headers={"Cache-Control": "no-cache"})

    async def api(request: web.Request) -> web.Response:
        error = await authorize(request)
        if error:
            return web.json_response({"ok": False, "error": error}, status=401 if error == "unauthorized" else 403)
        q = request.query
        tf = q.get("tf", "w")
        tf = tf if tf in TIMEFRAMES else "w"
        try:
            season = int(q.get("season", WINTER))
            offset = int(q.get("offset", "0"))
        except ValueError:
            return web.json_response({"ok": False, "error": "bad_request"}, status=400)
        season = season if season in SEASONS else WINTER
        offset = max(-MAX_BACK, min(0, offset))
        lang = q.get("lang", "uz") if q.get("lang") in ("uz", "ru") else "uz"
        started = time.perf_counter()
        data = await service.raw(tf, season, offset)
        payload = service.render(data, lang, offset)
        payload["ms"] = round((time.perf_counter() - started) * 1000, 1)
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/dashboard", page)
    app.router.add_get("/api/dashboard", api)
    app.router.add_get("/healthz", health)
    return app


class WebServer:
    """main.py: polling bilan birga ishga tushadi va to'xtatilganda toza yopiladi."""

    def __init__(self, app: web.Application, host: str, port: int) -> None:
        self._app, self._host, self._port = app, host, port
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()
        logger.info("Jonli Dashboard serveri ishga tushdi: http://%s:%d/dashboard", self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Jonli Dashboard serveri to'xtatildi")
