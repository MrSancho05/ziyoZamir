"""Boss/Admin uchun yig'ma hisobotlar (bitta xabar, bo'limlar joyida ochiladi).

* Haftalik / oylik yig'ma: 🏪 Do'kon xarajati + 📦 Ombor harakati + 👥 Davomat. Qisqacha ko'rinish (6–10 qator)
  va har bir bo'limning batafsil matni. Hech qanday formula ko'rsatilmaydi — faqat aniq raqamlar va taqqoslash.
* Kunlik kirim/chiqim (18:30): qisqacha + 📄 Batafsil.

Telegram va Google Sheets ga bog'liq emas: tayyor ma'lumotdan matn yasaydi (test qilish oson)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import escape
from typing import Dict, List, Optional, Sequence

from app.locales.texts import month_name, weekday_short
from app.services.attendance_grid import PeriodStats
from app.services.flows import FlowDelta, FlowSummary
from app.services.inspect_reports import fit_message, span
from app.services.payroll import fmt_money
from app.services.shop_grid import ShopStats
from app.services.warehouse import Product, UnpricedRow

WEEKLY, MONTHLY = "w", "m"


def _money(x: float) -> str:
    return fmt_money(int(round(x or 0)))


def _qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


@dataclass
class DigestData:
    kind: str                                    # w | m
    start: date
    end: date
    prev_start: date
    prev_end: date
    shop: Optional[ShopStats] = None
    shop_prev: Optional[ShopStats] = None
    flows: Dict[str, FlowSummary] = field(default_factory=dict)
    stock_value: Optional[float] = None
    low: List[Product] = field(default_factory=list)
    att: Optional[PeriodStats] = None
    errors: List[str] = field(default_factory=list)   # o'qib bo'lmagan bo'limlar: shop | wh | att


def _cmp(t: Dict[str, str], kind: str, cur: int, prev: Optional[int]) -> str:
    label = t["dg_label_w" if kind == WEEKLY else "dg_label_m"]
    if not prev:
        return t["dg_cmp_none"]
    diff = cur - prev
    pct = int(abs(diff) * 100 / prev + 0.5)
    if diff > 0:
        return t["dg_cmp_up"].format(diff=fmt_money(diff), pct=pct, label=label)
    if diff < 0:
        return t["dg_cmp_down"].format(diff=fmt_money(-diff), pct=pct, label=label)
    return t["dg_cmp_same"].format(label=label)


def _att_totals(stats: PeriodStats) -> tuple:
    came = sum(w.came for w in stats.workers)
    excused = sum(w.excused for w in stats.workers)
    absent = sum(w.absent for w in stats.workers)
    total = came + excused + absent
    return came, excused, absent, total, (round(came * 100 / total) if total else 0)


def _title(t: Dict[str, str], lang: str, d: DigestData) -> List[str]:
    head = (t["dg_title_w"] if d.kind == WEEKLY
            else t["dg_title_m"].format(month=month_name(lang, d.end.month).upper(), year=d.end.year))
    return [head, t["dg_period"].format(span=span(d.start, d.end))]


def render_digest(t: Dict[str, str], lang: str, d: DigestData) -> Dict[str, str]:
    """{"brief", "shop", "wh", "att"} — hammasi bitta xabarda joyida almashtiriladi."""
    return {"brief": _brief(t, lang, d), "shop": _shop_detail(t, lang, d), "wh": _wh_detail(t, lang, d),
            "att": _att_detail(t, lang, d)}


def _brief(t: Dict[str, str], lang: str, d: DigestData) -> str:
    lines = _title(t, lang, d) + [""]
    # 🏪 do'kon
    if "shop" in d.errors or d.shop is None:
        lines.append(t["dg_shop_head"].format(total="—"))
        lines.append(t["dg_section_error"])
    else:
        lines.append(t["dg_shop_head"].format(total=fmt_money(d.shop.total)))
        lines.append(_cmp(t, d.kind, d.shop.total, d.shop_prev.total if d.shop_prev and d.shop_prev.per_day else None))
    lines.append("")
    # 📦 ombor
    lines.append(t["dg_wh_head"])
    if "wh" in d.errors:
        lines.append(t["dg_section_error"])
    else:
        fin, fout = d.flows.get("inflow"), d.flows.get("outflow")
        lines.append(t["dg_wh_flow"].format(inflow=fmt_money(fin.amount if fin else 0),
                                            outflow=fmt_money(fout.amount if fout else 0)))
        if d.stock_value is not None:
            lines.append(t["dg_wh_stock"].format(value=_money(d.stock_value)))
        lines.append(t["dg_wh_low"].format(n=len(d.low)) if d.low else t["dg_wh_low_ok"])
    lines.append("")
    # 👥 davomat
    if "att" in d.errors or d.att is None or d.att.recorded_days == 0:
        lines.append(t["dg_att_none"])
    else:
        came, excused, absent, total, rate = _att_totals(d.att)
        lines.append(t["dg_att_head"].format(icon="✅" if rate >= 90 else "⚠️", rate=rate))
        lines.append(t["dg_att_abs"].format(n=absent) if absent else t["dg_att_abs_none"])
    lines += ["", t["dg_hint"]]
    return "\n".join(lines)


def _shop_detail(t: Dict[str, str], lang: str, d: DigestData) -> str:
    lines = _title(t, lang, d) + ["", t["dg_shop_title"], ""]
    if "shop" in d.errors or d.shop is None:
        return "\n".join(lines + [t["dg_section_error"]])
    cur = d.shop
    n = len(cur.per_day)
    lines.append(t["dg_shop_total"].format(total=fmt_money(cur.total)))
    if d.shop_prev is not None and d.shop_prev.per_day:
        lines.append(t["dg_shop_prev"].format(prev=fmt_money(d.shop_prev.total),
                                              span=span(d.prev_start, d.prev_end)))
    lines.append(_cmp(t, d.kind, cur.total, d.shop_prev.total if d.shop_prev and d.shop_prev.per_day else None))
    if n:
        lines += [t["dg_shop_days"].format(n=n), t["dg_shop_avg"].format(avg=fmt_money(round(cur.total / n)))]
    days = sorted(cur.per_day.items())
    if d.kind == WEEKLY and days:
        lines += ["", t["dg_shop_days_title"]]
        lines += [t["rep_shop_day_line"].format(date=x.strftime("%d.%m"), weekday=weekday_short(lang, x.weekday()),
                                                amount=fmt_money(a)) for x, a in days]
    elif days:
        lines += ["", t["rep_shop_top_title"]]
        lines += [t["rep_shop_day_line"].format(date=x.strftime("%d.%m"), weekday=weekday_short(lang, x.weekday()),
                                                amount=fmt_money(a)) for x, a in sorted(days, key=lambda y: -y[1])[:5]]
    if cur.missing:
        shown = ", ".join(x.strftime("%d.%m") for x in cur.missing[:15]) + (" …" if len(cur.missing) > 15 else "")
        lines += ["", t["rep_shop_missing"].format(n=len(cur.missing), days=shown)]
    return fit_message("\n".join(lines), t["an_truncated"])


def _wh_detail(t: Dict[str, str], lang: str, d: DigestData) -> str:
    lines = _title(t, lang, d) + ["", t["dg_wh_title"], ""]
    if "wh" in d.errors:
        return "\n".join(lines + [t["dg_section_error"]])
    for key, head, top_key in (("inflow", "dg_wh_in", "dg_wh_top_in"), ("outflow", "dg_wh_out", "dg_wh_top_out")):
        s = d.flows.get(key) or FlowSummary()
        lines.append(t[head].format(count=s.count, amount=fmt_money(s.amount)))
        if s.top:
            lines.append(t[top_key])
            lines += [t["dg_wh_top_line"].format(name=escape(name), qty=_qty(q), amount=fmt_money(a))
                      for name, q, a in s.top]
        lines.append("")
    if d.stock_value is not None:
        lines.append(t["dg_wh_stock_full"].format(value=_money(d.stock_value)))
    if d.low:
        lines += ["", t["dg_wh_low_title"].format(n=len(d.low))]
        lines += [t["dg_wh_low_line"].format(name=escape(p.name), stock=_qty(p.stock), unit=escape(p.unit),
                                             min=_qty(p.min_stock)) for p in d.low[:20]]
        if len(d.low) > 20:
            lines.append(f"… +{len(d.low) - 20}")
    else:
        lines.append(t["dg_wh_low_ok"].strip())
    return fit_message("\n".join(lines).strip(), t["an_truncated"])


def _att_detail(t: Dict[str, str], lang: str, d: DigestData) -> str:
    lines = _title(t, lang, d) + ["", t["dg_att_title"], ""]
    if "att" in d.errors or d.att is None or d.att.recorded_days == 0:
        return "\n".join(lines + [t["dg_att_none"]])
    stats = d.att
    came, excused, absent, total, rate = _att_totals(stats)
    lines += [t["dg_att_rate"].format(rate=rate, came=came, total=total),
              t["dg_att_counts"].format(came=came, excused=excused, absent=absent),
              t["dg_att_days"].format(rec=stats.recorded_days, work=stats.work_days)]
    offenders = sorted((w for w in stats.workers if w.absent), key=lambda w: (-w.absent, w.name))
    if offenders:
        lines += ["", t["dg_att_off_title"]]
        for w in offenders[:15]:
            days = ", ".join(x.strftime("%d.%m") for x in w.absent_days[:6])
            lines.append(t["dg_att_off_line"].format(name=escape(w.name), n=w.absent, days=days))
    excused_w = sorted((w for w in stats.workers if w.excused), key=lambda w: (-w.excused, w.name))
    if excused_w:
        lines += ["", t["dg_att_exc_title"]]
        lines += [t["dg_att_exc_line"].format(name=escape(w.name), n=w.excused) for w in excused_w[:15]]
    if stats.unrecorded:
        lines += ["", t["dg_att_unrec"].format(days=", ".join(x.strftime("%d.%m") for x in stats.unrecorded[:15]))]
    return fit_message("\n".join(lines), t["an_truncated"])


def pdf_lines(t: Dict[str, str], lang: str, d: DigestData) -> List[str]:
    """Oylik PDF uchun: barcha bo'limlar ketma-ket (HTML teglarisiz)."""
    import re
    sections = render_digest(t, lang, d)
    out: List[str] = []
    for key in ("brief", "shop", "wh", "att"):
        text = sections[key]
        if key != "brief":
            text = "\n".join(text.split("\n")[3:])        # sarlavha va davr har bo'limda takrorlanmasin
        else:
            text = "\n".join(text.split("\n")[:-2])       # "👇 bo'limni tanlang" PDF da kerak emas
        out += [re.sub(r"<[^>]+>", "", line) for line in text.split("\n")] + [""]
    return out


# ---------------- kunlik kirim / chiqim (18:30) ----------------
def render_flows_daily(t: Dict[str, str], day: date, deltas: Dict[str, FlowDelta], detail: str,
                       unpriced: Sequence[UnpricedRow]) -> Dict[str, str]:
    """Qisqacha (narxi yo'q tovarlar ogohlantirishi tepada) va batafsil."""
    warn = [t["fd_unpriced_warn"].format(item=escape(r.name)) for r in unpriced[:5]]
    if len(unpriced) > 5:
        warn.append(t["fd_unpriced_more"].format(n=len(unpriced) - 5))
    lines = [t["fd_brief_title"].format(date=day.strftime("%d.%m.%Y")), ""]
    if warn:
        lines += warn + [""]
    for key, label in (("inflow", "fd_brief_in"), ("outflow", "fd_brief_out")):
        dl = deltas.get(key)
        rows = dl.new_rows if dl and dl.sheet_found else []
        lines.append(t[label].format(count=len(rows), amount=fmt_money(sum(r.amount or 0 for r in rows))))
    ins = deltas.get("inflow")
    top = sorted((r for r in (ins.new_rows if ins else []) if r.amount), key=lambda r: -(r.amount or 0))[:3]
    if top:
        lines.append(t["fd_brief_top"].format(items=", ".join(escape(r.label or r.text) for r in top)))
    full = "\n".join(warn + [""] + [detail]) if warn else detail
    return {"brief": "\n".join(lines), "detail": fit_message(full, t["an_truncated"])}


# ---------------- 20:00 kunlik ijroiya xulosasi ----------------
@dataclass
class ExecDay:
    """Bir kunning hamma bo'limlari: do'kon, davomat, ombor. None — bo'limni o'qib bo'lmadi."""
    day: date
    shop_entered: Optional[bool] = None
    shop_amount: Optional[int] = None
    shop_month: Optional[int] = None
    att_recorded: Optional[bool] = None
    came: List[str] = field(default_factory=list)
    absent: List[str] = field(default_factory=list)
    excused: List[str] = field(default_factory=list)
    unmarked: int = 0
    deltas: Optional[Dict[str, FlowDelta]] = None
    unpriced_today: List[UnpricedRow] = field(default_factory=list)
    unpriced_total: int = 0
    low: List[Product] = field(default_factory=list)
    stock_value: Optional[float] = None
    errors: List[str] = field(default_factory=list)


def _flow_totals(d: ExecDay, key: str) -> tuple:
    dl = (d.deltas or {}).get(key)
    rows = dl.new_rows if dl and dl.sheet_found else []
    return len(rows), sum(r.amount or 0 for r in rows), rows


def render_exec_summary(t: Dict[str, str], lang: str, d: ExecDay) -> Dict[str, str]:
    """Qisqacha (6–10 qator, har bo'limda ✅ / ⚠️) va batafsil (ismlar, tovarlar). Formulalarsiz."""
    from app.locales.texts import weekday_name
    head = [t["es_title"].format(date=d.day.strftime("%d.%m.%Y"), weekday=weekday_name(lang, d.day.weekday())), ""]
    issues = 0

    # 🏪 do'kon
    if "shop" in d.errors or d.shop_entered is None:
        shop_line = t["es_shop_err"]
    elif d.shop_entered:
        shop_line = t["es_shop_ok"].format(amount=fmt_money(d.shop_amount or 0),
                                           month=fmt_money(d.shop_month or 0))
    else:
        shop_line = t["es_shop_missing"]
        issues += 1

    # 👥 davomat
    saturday = d.day.weekday() == 5
    if "att" in d.errors or d.att_recorded is None:
        att_line = t["es_att_err"]
    elif not d.att_recorded:
        att_line = t["es_att_sat_none"] if saturday else t["es_att_missing"]
        issues += 0 if saturday else 1
    elif saturday:
        att_line = t["es_att_sat"].format(n=len(d.came))
    else:
        total = len(d.came) + len(d.absent) + len(d.excused)
        rate = round(len(d.came) * 100 / total) if total else 0
        if d.absent:
            issues += 1
        att_line = t["es_att_ok" if not d.absent else "es_att_warn"].format(
            rate=rate, came=len(d.came), total=total, absent=len(d.absent), excused=len(d.excused))

    # 📦 ombor
    n_in, a_in, rows_in = _flow_totals(d, "inflow")
    n_out, a_out, rows_out = _flow_totals(d, "outflow")
    if "flows" in d.errors or d.deltas is None:
        wh_line = t["es_wh_err"]
    elif n_in + n_out == 0:
        wh_line = t["es_wh_none"]
        issues += 1
    else:
        wh_line = t["es_wh_ok"].format(n_in=n_in, a_in=fmt_money(a_in), n_out=n_out, a_out=fmt_money(a_out))
    extra = []
    if d.unpriced_total:
        extra.append(t["es_unpriced"].format(n=d.unpriced_total))
        issues += 1
    if d.low:
        extra.append(t["es_low"].format(n=len(d.low)))

    verdict = t["es_all_ok"] if issues == 0 else t["es_issues"].format(n=issues)
    brief = "\n".join(head + [shop_line, att_line, wh_line] + extra + ["", verdict])

    # ---- batafsil ----
    lines = head + [t["es_d_shop"], shop_line]
    lines += ["", t["es_d_att"], att_line]
    if d.att_recorded and not saturday:
        if d.absent:
            lines.append(t["es_d_absent"].format(names=", ".join(escape(n) for n in d.absent)))
        if d.excused:
            lines.append(t["es_d_excused"].format(names=", ".join(escape(n) for n in d.excused)))
        if d.unmarked:
            lines.append(t["es_d_unmarked"].format(n=d.unmarked))
    elif d.att_recorded and saturday and d.came:
        lines.append(t["es_d_came"].format(names=", ".join(escape(n) for n in d.came)))
    lines += ["", t["es_d_wh"], wh_line]
    for title_key, rows in (("es_d_in", rows_in), ("es_d_out", rows_out)):
        if rows:
            lines.append(t[title_key])
            for r in sorted(rows, key=lambda r: -(r.amount or 0))[:8]:
                qty = f" — {_qty(r.qty)}" if r.qty is not None else ""
                amount = f" · {fmt_money(r.amount)} " + t["es_cur"] if r.amount else ""
                lines.append(f"• {escape(r.label or r.text)}{qty}{amount}")
            if len(rows) > 8:
                lines.append(f"… +{len(rows) - 8}")
    if d.unpriced_today or d.unpriced_total:
        lines += ["", t["es_unpriced"].format(n=d.unpriced_total)]
        lines += [t["fd_unpriced_warn"].format(item=escape(r.name)) for r in d.unpriced_today[:5]]
    if d.stock_value is not None:
        lines += ["", t["dg_wh_stock_full"].format(value=_money(d.stock_value))]
    if d.low:
        lines += [t["dg_wh_low_title"].format(n=len(d.low))]
        lines += [t["dg_wh_low_line"].format(name=escape(p.name), stock=_qty(p.stock), unit=escape(p.unit),
                                             min=_qty(p.min_stock)) for p in d.low[:10]]
        if len(d.low) > 10:
            lines.append(f"… +{len(d.low) - 10}")
    lines += ["", verdict]
    return {"brief": brief, "detail": fit_message("\n".join(lines), t["an_truncated"])}
