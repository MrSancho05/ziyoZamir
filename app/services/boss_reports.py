"""Bossga (va uning nusxasi sifatida Adminga) yuboriladigan hisobotlarning matnlari va yuborish funksiyasi."""
import logging
from datetime import date
from html import escape
from typing import Any, Callable, Optional, Tuple

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.locales.texts import get_texts, month_name, weekday_short
from app.services.access import safe_send
from app.services.attendance_grid import PLUS, EXCUSED, MINUS, is_saturday
from app.services.attendance_report import format_daily, format_saturday
from app.services.flows import FlowDelta, FlowSummary
from app.services.flows_grid import FlowRow
from app.services.notify import boss_circle
from app.services.payroll import fmt_money
from app.services.shop_grid import ShopStats
from app.storage.users import UserRepository
from app.utils.text import split_message

logger = logging.getLogger(__name__)


async def _send_parts(bot: Bot, chat_id: int, text: str, markup: Any = None) -> bool:
    """Uzun matn bo'laklarga bo'linadi; tugmalar oxirgi bo'lakka qo'yiladi."""
    ok = True
    parts = split_message(text)
    for i, part in enumerate(parts):
        extra = {"reply_markup": markup} if markup is not None and i == len(parts) - 1 else {}
        ok = await safe_send(bot, chat_id, part, **extra) and ok
    return ok


async def _send_document(bot: Bot, chat_id: int, doc: Optional[Tuple[bytes, str, str]]) -> None:
    if not doc:
        return
    data, filename, caption = doc
    try:
        await bot.send_document(chat_id, BufferedInputFile(data, filename=filename), caption=caption)
    except Exception:
        logger.warning("Hujjatni %s ga yuborib bo'lmadi", chat_id, exc_info=True)


async def send_to_bosses(bot: Bot, repo: UserRepository, build: Callable[[dict[str, str], str], str],
                         markup: Optional[Callable[[dict[str, str], str], Any]] = None,
                         document: Optional[Callable[[dict[str, str], str], Optional[Tuple[bytes, str, str]]]] = None
                         ) -> int:
    """Matnni har bir bossga uning tilida yuboradi va xuddi shu xabarning NUSXASINI Adminlarga ham yuboradi
    (admin boss ro'yxatida bo'lsa, ikki marta yuborilmaydi). Bossga yetkazilganlar soni qaytariladi
    (admin nusxalari sanalmaydi). Nusxa faqat boss(lar)ga yetib borgandan keyin yuboriladi: shunda xabar
    qayta uriniladigan bo'lsa, admin bir xil xabarni ko'p marta olmaydi."""
    bosses, mirrors = await boss_circle(repo)
    delivered = 0

    async def deliver(user) -> bool:
        t = get_texts(user.lang)
        ok = await _send_parts(bot, user.id, build(t, user.lang), markup(t, user.lang) if markup else None)
        if ok and document is not None:
            await _send_document(bot, user.id, document(t, user.lang))
        return ok

    for boss in bosses:
        if await deliver(boss):
            delivered += 1
    if delivered:
        for admin in mirrors:
            await deliver(admin)
    return delivered


# ---------------- taqqoslash ----------------
def cmp_line(t: dict[str, str], label: str, total: int, prev: int) -> str:
    """"Oldingi davrga nisbatan qancha ko'p / kam" qatori."""
    if prev == 0:
        return t["rep_cmp_noprev"].format(label=label)
    diff = total - prev
    pct = round(abs(diff) * 100 / prev)
    if diff > 0:
        return t["rep_cmp_up"].format(label=label, diff=fmt_money(diff), pct=pct, prev=fmt_money(prev))
    if diff < 0:
        return t["rep_cmp_down"].format(label=label, diff=fmt_money(-diff), pct=pct, prev=fmt_money(prev))
    return t["rep_cmp_same"].format(label=label, prev=fmt_money(prev))


# ---------------- yo'qlama (kunlik) ----------------
def attendance_day_text(t: dict[str, str], day: date, names: list[str], marks: list[str | None],
                        by_name: str | None) -> str:
    pairs = list(zip(names, marks))
    came = [n for n, m in pairs if m == PLUS]
    if is_saturday(day):
        return format_saturday(t, day, came, by_name)
    return format_daily(t, day, came, [n for n, m in pairs if m == MINUS],
                        [n for n, m in pairs if m == EXCUSED], by_name)


# ---------------- do'kon xarajatlari ----------------
def format_shop_daily(t: dict[str, str], day: date, amount: int, month_total: int) -> str:
    return "\n".join([
        t["rep_shop_daily_title"].format(date=day.strftime("%d.%m.%Y")),
        "",
        t["rep_shop_day_amount"].format(amount=fmt_money(amount)),
        t["rep_shop_month_to_date"].format(total=fmt_money(month_total)),
    ])


def format_shop_period(t: dict[str, str], lang: str, title: str, stats: ShopStats,
                       prev: ShopStats | None, kind: str) -> str:
    """kind: weekly | monthly | yearly. Oldingi davr (prev) bilan taqqoslanadi."""
    lines = [title, ""]
    for year, month in stats.months_missing:
        lines.append(t["rep_shop_nosheet"].format(tab=f"{month_name(lang, month)} {year}"))
    lines.append(t["rep_shop_total"].format(total=fmt_money(stats.total)))
    if stats.per_day:
        lines.append(t["rep_shop_days"].format(n=len(stats.per_day)))
        lines.append(t["rep_shop_avg"].format(avg=fmt_money(round(stats.total / len(stats.per_day)))))
    if prev is not None:
        lines.append(cmp_line(t, t[f"cmp_label_{ {'weekly': 'week', 'monthly': 'month', 'yearly': 'year'}[kind] }"],
                              stats.total, prev.total))
    lines.append("")

    days = sorted(stats.per_day.items())
    if kind == "weekly":
        lines += [t["rep_shop_day_line"].format(date=d.strftime("%d.%m"), weekday=weekday_short(lang, d.weekday()),
                                                amount=fmt_money(a)) for d, a in days]
    elif kind == "monthly":
        top = sorted(days, key=lambda x: -x[1])[:5]
        if top:
            lines.append(t["rep_shop_top_title"])
            lines += [t["rep_shop_day_line"].format(date=d.strftime("%d.%m"), weekday=weekday_short(lang, d.weekday()),
                                                    amount=fmt_money(a)) for d, a in top]
    else:
        per_month: dict[int, int] = {}
        for d, a in days:
            per_month[d.month] = per_month.get(d.month, 0) + a
        if per_month:
            lines.append(t["rep_shop_months_title"])
            lines += [t["rep_shop_month_line"].format(month=month_name(lang, m), amount=fmt_money(a))
                      for m, a in sorted(per_month.items())]

    if stats.missing:
        shown = ", ".join(d.strftime("%d.%m") for d in stats.missing[:20])
        if len(stats.missing) > 20:
            shown += " …"
        lines += ["", t["rep_shop_missing"].format(n=len(stats.missing), days=shown)]
    return "\n".join(lines).strip()


# ---------------- kirim / chiqim ----------------
def _qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


def _flow_line(t: dict[str, str], r: FlowRow, cols_found: bool) -> str:
    if cols_found and r.label:
        line = f"• {escape(r.label)}"
        if r.qty is not None:
            line += t["rep_flow_qty"].format(qty=_qty(r.qty))
        if r.price is not None:
            line += t["rep_flow_price"].format(price=fmt_money(r.price))
        if r.amount is not None:
            line += t["rep_flow_amount"].format(amount=fmt_money(r.amount))
        return line
    return f"• {escape(r.text)}"


def format_flows_daily(t: dict[str, str], day: date, deltas: dict[str, FlowDelta],
                       tabs: dict[str, str], max_lines: int = 30) -> str:
    lines = [t["rep_flow_daily_title"].format(date=day.strftime("%d.%m.%Y")), ""]
    for key, title_key in (("inflow", "rep_flow_in_title"), ("outflow", "rep_flow_out_title")):
        d = deltas[key]
        lines.append(t[title_key])
        if not d.sheet_found:
            lines.append(t["rep_flow_sheet_missing"].format(tab=escape(tabs[key])))
        elif not d.new_rows:
            lines.append(t["rep_flow_none"])
        else:
            lines += [_flow_line(t, r, d.cols_found) for r in d.new_rows[:max_lines]]
            if len(d.new_rows) > max_lines:
                lines.append(t["rep_flow_more"].format(n=len(d.new_rows) - max_lines))
            lines.append(t["rep_flow_total"].format(
                count=len(d.new_rows), amount=fmt_money(sum(r.amount or 0 for r in d.new_rows))))
            if not d.cols_found:
                lines.append(t["rep_flow_cols_warn"])
        lines.append("")
    return "\n".join(lines).strip()


def format_flows_period(t: dict[str, str], lang: str, title: str, cur: dict[str, FlowSummary],
                        prev: dict[str, FlowSummary], label_key: str) -> str:
    lines = [title, ""]
    for key, total_key, name_key in (("inflow", "rep_flow_in_total", "flow_in_name"),
                                     ("outflow", "rep_flow_out_total", "flow_out_name")):
        s = cur[key]
        lines.append(t[total_key].format(count=s.count, amount=fmt_money(s.amount)))
        lines.append(cmp_line(t, f"{t[name_key]} — {t[label_key]}", s.amount, prev[key].amount))
        lines.append("")
    for key, top_key in (("inflow", "rep_flow_top_in"), ("outflow", "rep_flow_top_out")):
        top = cur[key].top
        if top:
            lines.append(t[top_key])
            lines += [t["rep_flow_top_line"].format(label=escape(label), qty=_qty(q), amount=fmt_money(a))
                      for label, q, a in top]
            lines.append("")
    return "\n".join(lines).strip()
