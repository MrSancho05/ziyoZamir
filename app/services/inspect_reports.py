"""Boss/Admin tahlili: do'kon xarajatlari va davomat uchun ikki ko'rinish.

* ``brief``  — egasi uchun eng muhim qisqa xulosa (ortiqcha tafsilotsiz);
* ``detail`` — to'liq tahlil: taqqoslash, foizlar, ishchilar bo'yicha sanalar va oddiy tilda hisoblash tartibi.

Google Sheets va Telegramga bog'liq emas: tayyor statistikadan faqat matn yasaydi.
"""
from dataclasses import dataclass
from datetime import date
from html import escape

from app.locales.texts import month_name, season_name, weekday_name, weekday_short
from app.services.attendance_grid import PeriodStats
from app.services.attendance_report import _payroll_block, format_daily, format_period, format_saturday
from app.services.payroll import MonthReport, fmt_money
from app.services.shop_grid import ShopStats
from app.utils.periods import (
    TF_DAILY,
    TF_MONTHLY,
    TF_SEASONAL,
    TF_WEEKLY,
    WINTER,
    Range,
)

# Telegram xabari 4096 belgidan oshmasligi kerak. Xabar o'sha joyning o'zida tahrirlanadi (yangi xabar yuborilmaydi),
# shuning uchun batafsil ko'rinish bitta xabarga sig'ishi shart. HTML teglari uchun zaxira qoldiriladi.
MAX_LEN = 4000
DATE_CAPS = (None, 12, 6, 3, 1)       # batafsil ko'rinish sig'masa, sanalar ro'yxati shu tartibda qisqartiriladi
TOP_NAMES = 3

_KEYCAPS = tuple(f"{i}\ufe0f\u20e3" for i in range(1, 10))


@dataclass(frozen=True, slots=True)
class Rendered:
    """Bitta tahlilning tayyor matnlari. `empty` bo'lsa ma'lumot yo'q: faqat shu xabar ko'rsatiladi."""
    brief: str = ""
    detail: str = ""
    empty: str | None = None


# ---------------- umumiy yordamchilar ----------------
def span(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%d.%m.%Y")
    if start.year == end.year:
        return f"{start.strftime('%d.%m')} — {end.strftime('%d.%m.%Y')}"
    return f"{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}"


def period_label(t: dict[str, str], lang: str, rng: Range) -> str:
    dates = span(rng.start, rng.end)
    if rng.tf == TF_DAILY:
        return t["an_p_daily"].format(date=rng.start.strftime("%d.%m.%Y"),
                                      weekday=weekday_name(lang, rng.start.weekday()))
    if rng.tf == TF_WEEKLY:
        return t["an_p_weekly"].format(span=dates)
    if rng.tf == TF_MONTHLY:
        return t["an_p_monthly"].format(month=month_name(lang, rng.end.month), year=rng.end.year, span=dates)
    if rng.tf == TF_SEASONAL:
        year = rng.season_year
        years = f"{year}–{year + 1}" if rng.season == WINTER else str(year)
        return t["an_p_seasonal"].format(season=season_name(lang, rng.season), years=years, span=dates)
    return t["an_p_yearly"].format(year=rng.end.year, span=dates)


def fit_message(text: str, footer: str, limit: int = MAX_LEN) -> str:
    """Matn sig'masa, qator chegarasida kesib, izoh qo'shadi (HTML teglari buzilmasligi uchun faqat butun qatorlar)."""
    if len(text) <= limit:
        return text
    kept: list[str] = []
    size = len(footer) + 2
    for line in text.split("\n"):
        if size + len(line) + 1 > limit:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept).rstrip() + "\n\n" + footer


def _steps(t: dict[str, str], items: list[str]) -> list[str]:
    """Hisoblash tartibi: sarlavha va raqamlangan qadamlar."""
    return [t["an_method_title"], *[f"{_KEYCAPS[i]} {text}" for i, text in enumerate(items)]]


def _signed(n: int) -> str:
    return ("+" if n > 0 else "−" if n < 0 else "") + fmt_money(abs(n))


def _pct(part: int, whole: int) -> int:
    return int(part * 100 / whole + 0.5)


# ---------------- do'kon xarajatlari ----------------
def _shop_title(t: dict[str, str], lang: str, rng: Range) -> str:
    return t["an_title_shop"].format(period=period_label(t, lang, rng))


def _shop_empty(t: dict[str, str], lang: str, rng: Range, cur: ShopStats) -> str:
    lines = [_shop_title(t, lang, rng), "", t["an_shop_empty"]]
    for year, month in cur.months_missing:
        lines.append(t["rep_shop_nosheet"].format(tab=f"{month_name(lang, month)} {year}"))
    if cur.missing:
        shown = ", ".join(d.strftime("%d.%m") for d in cur.missing[:20])
        lines.append(t["rep_shop_missing"].format(n=len(cur.missing), days=shown + (" …" if len(cur.missing) > 20 else "")))
    return "\n".join(lines)


def _shop_breakdown(t: dict[str, str], lang: str, rng: Range, cur: ShopStats) -> list[str]:
    days = sorted(cur.per_day.items())
    if rng.tf == TF_WEEKLY:
        return [t["rep_shop_day_line"].format(date=d.strftime("%d.%m"), weekday=weekday_short(lang, d.weekday()),
                                              amount=fmt_money(a)) for d, a in days]
    if rng.tf == TF_MONTHLY:
        top = sorted(days, key=lambda x: -x[1])[:5]
        return ([t["rep_shop_top_title"]]
                + [t["rep_shop_day_line"].format(date=d.strftime("%d.%m"), weekday=weekday_short(lang, d.weekday()),
                                                 amount=fmt_money(a)) for d, a in top]) if top else []
    if rng.tf == TF_DAILY:
        return []
    per_month: dict[tuple[int, int], int] = {}
    for d, a in days:
        per_month[(d.year, d.month)] = per_month.get((d.year, d.month), 0) + a
    with_year = rng.tf == TF_SEASONAL
    return [t["rep_shop_months_title"]] + [
        t["rep_shop_month_line"].format(month=month_name(lang, m) + (f" {y}" if with_year else ""),
                                        amount=fmt_money(a))
        for (y, m), a in sorted(per_month.items())]


def _shop_detail(t: dict[str, str], lang: str, rng: Range, cur: ShopStats, prev: ShopStats) -> str:
    n = len(cur.per_day)
    avg = round(cur.total / n)
    lines = [_shop_title(t, lang, rng), "", t["an_shop_total"].format(total=fmt_money(cur.total))]
    if rng.tf != TF_DAILY:
        lines += [t["rep_shop_days"].format(n=n), t["an_shop_avg"].format(avg=fmt_money(avg))]

    # --- oldingi davr bilan taqqoslash ---
    lines += ["", t["an_cmp_title"],
              t["an_cmp_vs"].format(what=t[f"an_prev_{rng.tf}"], span=span(rng.prev_start, rng.prev_end))]
    diff = cur.total - prev.total
    has_prev = bool(prev.per_day)
    if not has_prev:
        lines.append(t["an_cmp_noprev"])
    else:
        lines.append(t["an_cmp_prev_total"].format(total=fmt_money(prev.total)))
        if prev.total == 0:
            lines.append(t["an_cmp_prevzero"].format(diff=_signed(diff)))
        elif diff > 0:
            lines.append(t["an_cmp_up"].format(diff=fmt_money(diff), pct=_pct(diff, prev.total)))
        elif diff < 0:
            lines.append(t["an_cmp_down"].format(diff=fmt_money(-diff), pct=_pct(-diff, prev.total)))
        else:
            lines.append(t["an_cmp_same"])

    breakdown = _shop_breakdown(t, lang, rng, cur)
    if breakdown:
        lines += ["", *breakdown]
    for year, month in cur.months_missing:
        lines += ["", t["rep_shop_nosheet"].format(tab=f"{month_name(lang, month)} {year}")]
    if cur.missing:
        shown = ", ".join(d.strftime("%d.%m") for d in cur.missing[:20]) + (" …" if len(cur.missing) > 20 else "")
        lines += ["", t["rep_shop_missing"].format(n=len(cur.missing), days=shown)]

    # --- hisoblash tartibi (oddiy tilda) ---
    steps = [t["an_shop_step_src"], t["an_shop_step_total"].format(total=fmt_money(cur.total))]
    if rng.tf != TF_DAILY:
        steps.append(t["an_shop_step_avg"].format(total=fmt_money(cur.total), n=n, avg=fmt_money(avg)))
    if has_prev:
        steps.append(t["an_shop_step_diff"].format(cur=fmt_money(cur.total), prev=fmt_money(prev.total),
                                                   diff=_signed(diff)))
        if prev.total > 0:
            steps.append(t["an_shop_step_pct"].format(diff=fmt_money(abs(diff)), prev=fmt_money(prev.total),
                                                      pct=_pct(abs(diff), prev.total)))
    lines += ["", *_steps(t, steps)]
    return fit_message("\n".join(lines), t["an_truncated"])


def shop_rendered(t: dict[str, str], lang: str, rng: Range, cur: ShopStats, prev: ShopStats) -> Rendered:
    """Qisqacha: faqat umumiy xarajat (UZS). Batafsil: oldingi davr bilan taqqoslash va hisoblash tartibi."""
    if not cur.per_day:
        return Rendered(empty=_shop_empty(t, lang, rng, cur))
    brief = "\n".join([_shop_title(t, lang, rng), "", t["an_shop_total"].format(total=fmt_money(cur.total))])
    return Rendered(brief=brief, detail=_shop_detail(t, lang, rng, cur, prev))


# ---------------- davomat ----------------
def _att_title(t: dict[str, str], lang: str, rng: Range) -> str:
    return t["an_title_att"].format(period=period_label(t, lang, rng))


def _att_empty(t: dict[str, str], lang: str, rng: Range, stats: PeriodStats) -> str:
    lines = [_att_title(t, lang, rng), "", t["an_att_empty"]]
    for year, month in stats.missing:
        lines.append(t["rep_missing_tab"].format(tab=f"{month_name(lang, month)} {year}"))
    return "\n".join(lines)


def _rate(stats: PeriodStats) -> tuple[int, int, int, int, int]:
    came = sum(w.came for w in stats.workers)
    excused = sum(w.excused for w in stats.workers)
    absent = sum(w.absent for w in stats.workers)
    total = came + excused + absent
    rate = round(came * 100 / total) if total else 0
    return came, excused, absent, total, rate


def _att_daily(t: dict[str, str], lang: str, rng: Range, stats: PeriodStats) -> Rendered:
    day = rng.start
    title = _att_title(t, lang, rng)
    if day.weekday() == 5:                                        # shanba: faqat ishlaganlar
        came_names = [w.name for w in stats.workers if w.saturday_days]
        if not came_names:
            return Rendered(empty=t["view_sat_none"].format(date=day.strftime("%d.%m.%Y")))
        brief = "\n".join([title, "", t["an_att_sat_brief"].format(n=len(came_names))])
        detail = format_saturday(t, day, came_names).split("\n")
        detail[0] = title                                           # sarlavha: davr nomi bilan
        return Rendered(brief=brief, detail=fit_message("\n".join(detail), t["an_truncated"]))

    if stats.recorded_days == 0:
        return Rendered(empty=t["view_today_none"].format(date=day.strftime("%d.%m.%Y")))

    came = [w.name for w in stats.workers if w.came]
    unexcused = [w.name for w in stats.workers if w.absent]
    excused = [w.name for w in stats.workers if w.excused]
    unmarked = [w.name for w in stats.workers if w.unmarked]

    # --- qisqacha ---
    brief_lines = [title, ""]
    if not unexcused and not excused:
        brief_lines.append(t["an_att_day_all"].format(n=len(came)))
    else:
        brief_lines.append(t["an_att_day_counts"].format(came=len(came), absent=len(unexcused) + len(excused)))
        brief_lines.append(t["an_att_day_absent"].format(names=", ".join(escape(n) for n in unexcused + excused)))

    # --- batafsil ---
    detail = format_daily(t, day, came, unexcused, excused).split("\n")
    detail[0] = title                                               # sarlavha: davr nomi bilan
    if unmarked:
        detail += ["", t["rep_unmarked_title"], ", ".join(escape(n) for n in unmarked)]
    c, e, a, total, rate = _rate(stats)
    detail += ["", t["rep_sum_rate"].format(rate=rate, came=c, total=total), "",
               *_steps(t, [t["an_att_step_total"].format(came=c, excused=e, absent=a, total=total),
                           t["an_att_step_rate"].format(came=c, total=total, rate=rate)])]
    return Rendered(brief="\n".join(brief_lines), detail=fit_message("\n".join(detail), t["an_truncated"]))


def _att_brief_period(t: dict[str, str], lang: str, rng: Range, stats: PeriodStats) -> str:
    lines = [_att_title(t, lang, rng), ""]
    unexcused = sum(w.absent for w in stats.workers)
    excused = sum(w.excused for w in stats.workers)
    if not unexcused and not excused:
        lines.append(t["rep_all_came"])
    else:
        lines.append(t["an_att_brief_absent"].format(total=unexcused + excused, unexcused=unexcused, excused=excused))
        offenders = sorted((w for w in stats.workers if w.absent), key=lambda w: (-w.absent, -w.excused, w.name))
        if offenders:
            top = ", ".join(f"{escape(w.name)} ({w.absent})" for w in offenders[:TOP_NAMES])
            if len(offenders) > TOP_NAMES:
                top += f" … +{len(offenders) - TOP_NAMES}"
            lines.append(t["an_att_brief_top"].format(names=top))
    if stats.unrecorded:
        lines.append(t["an_att_brief_unrecorded"].format(n=len(stats.unrecorded)))
    return "\n".join(lines)


def _att_detail_period(t: dict[str, str], lang: str, rng: Range, stats: PeriodStats,
                       report: MonthReport | None) -> str:
    title = _att_title(t, lang, rng)
    came, excused, absent, total, rate = _rate(stats)
    steps = [t["an_att_step_workdays"].format(work=stats.work_days),
             t["an_att_step_recorded"].format(n=stats.recorded_days),
             t["an_att_step_total"].format(came=came, excused=excused, absent=absent, total=total),
             t["an_att_step_rate"].format(came=came, total=total, rate=rate)]
    if any(w.saturday_days for w in stats.workers):
        steps.append(t["pay_sat_rule"].removeprefix("🛠 "))      # raqamli qadamda ikkita emoji bo'lmasin
    method = _steps(t, steps)

    payroll: list[str] = []
    if report is not None and report.tab_found:
        payroll = [""] + (_payroll_block(t, report).split("\n") + ["", t["an_pay_steps"].format(work=report.work_days)]
                          if report.pays is not None else [t["pay_no_cols"]])

    text = ""
    for cap in DATE_CAPS:                                         # sig'maguncha sanalarni bosqichma-bosqich qisqartiramiz
        top = format_period(t, lang, title, stats, max_dates=cap).replace(
            t["rep_summary_title"], t["an_att_overview"], 1)
        text = "\n".join([top, *payroll, "", *method])
        if len(text) <= MAX_LEN:
            return text
    return fit_message(text, t["an_truncated"])


def attendance_rendered(t: dict[str, str], lang: str, rng: Range, stats: PeriodStats,
                        report: MonthReport | None = None) -> Rendered:
    """Qisqacha: egasi uchun asosiy xulosa (formula va foizsiz). Batafsil: foizlar, formulalar, ishchilar sanalari."""
    if rng.tf == TF_DAILY:
        return _att_daily(t, lang, rng, stats)
    if stats.recorded_days == 0:
        return Rendered(empty=_att_empty(t, lang, rng, stats))
    return Rendered(brief=_att_brief_period(t, lang, rng, stats),
                    detail=_att_detail_period(t, lang, rng, stats, report))
