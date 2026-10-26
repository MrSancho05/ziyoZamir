from datetime import date
from html import escape

from app.locales.texts import month_name
from app.services.attendance_grid import PeriodStats
from app.services.payroll import MonthReport, fmt_money


def format_daily(t: dict[str, str], day: date, came: list[str], unexcused: list[str],
                 excused: list[str], by_name: str | None = None) -> str:
    def block(names: list[str]) -> list[str]:
        return [f"{i}. {escape(n)}" for i, n in enumerate(names, 1)] or [t["rep_nobody"]]

    lines = [
        t["rep_daily_title"].format(date=day.strftime("%d.%m.%Y")),
        "",
        t["rep_came"].format(n=len(came)),
        t["rep_absent"].format(n=len(unexcused) + len(excused),
                               excused=len(excused), unexcused=len(unexcused)),
        "",
        t["rep_came_list"], *block(came),
        "",
        t["rep_unexcused_list"], *block(unexcused),
        "",
        t["rep_excused_list"], *block(excused),
    ]
    if by_name:
        lines += ["", t["rep_by"].format(name=escape(by_name))]
    return "\n".join(lines)


def format_saturday(t: dict[str, str], day: date, came: list[str], by_name: str | None = None) -> str:
    """Shanba (ixtiyoriy ish kuni): faqat ishlaganlar ro'yxati."""
    lines = [
        t["rep_sat_title"].format(date=day.strftime("%d.%m.%Y")),
        "",
        t["rep_sat_came"].format(n=len(came)),
        *([f"{i}. {escape(n)}" for i, n in enumerate(came, 1)] or [t["rep_nobody"]]),
        "",
        t["rep_sat_note"],
    ]
    if by_name:
        lines += ["", t["rep_by"].format(name=escape(by_name))]
    return "\n".join(lines)


def _dates(days: list[date]) -> str:
    return ", ".join(d.strftime("%d.%m") for d in days)


def format_period(t: dict[str, str], lang: str, title: str, stats: PeriodStats) -> str:
    """Haftalik / oylik hisobot: avval qisqacha xulosa, keyin kimlar kelmagani (sanalari bilan),
    oxirida hamma kunlarda kelganlar."""
    lines = [title, ""]

    if stats.recorded_days == 0:
        lines.append(t["rep_no_data"])
        if stats.unrecorded:
            lines.append(t["rep_sum_unrecorded"].format(n=len(stats.unrecorded),
                                                       days=_dates(stats.unrecorded)))
    else:
        came = sum(w.came for w in stats.workers)
        total = sum(w.came + w.excused + w.absent for w in stats.workers)
        rate = round(came * 100 / total) if total else 0

        lines += [
            t["rep_summary_title"],
            t["rep_sum_workdays"].format(work=stats.work_days),
            t["rep_sum_recorded"].format(n=stats.recorded_days),
        ]
        if stats.unrecorded:
            lines.append(t["rep_sum_unrecorded"].format(n=len(stats.unrecorded),
                                                       days=_dates(stats.unrecorded)))
        lines += [
            t["rep_sum_workers"].format(n=len(stats.workers)),
            t["rep_sum_rate"].format(rate=rate, came=came, total=total),
            "",
        ]

        unexcused = [w for w in stats.workers if w.absent]
        excused = [w for w in stats.workers if w.excused]
        unmarked = [w for w in stats.workers if w.unmarked]
        always = [w for w in stats.workers if not (w.absent or w.excused or w.unmarked)]

        if unexcused:
            lines.append(t["rep_unexcused_list"])
            lines += [t["rep_person_days"].format(name=escape(w.name), n=w.absent,
                                                  dates=_dates(w.absent_days)) for w in unexcused]
            lines.append("")
        if excused:
            lines.append(t["rep_excused_list"])
            lines += [t["rep_person_days"].format(name=escape(w.name), n=w.excused,
                                                  dates=_dates(w.excused_days)) for w in excused]
            lines.append("")
        if unmarked:
            lines.append(t["rep_unmarked_title"])
            lines += [t["rep_unmarked_line"].format(name=escape(w.name), n=w.unmarked)
                      for w in unmarked]
            lines.append("")
        saturday = [w for w in stats.workers if w.saturday_days]
        if saturday:
            lines.append(t["rep_saturday_title"])
            lines += [t["rep_person_days"].format(name=escape(w.name), n=len(w.saturday_days),
                                                  dates=_dates(w.saturday_days)) for w in saturday]
            lines.append("")
        if not unexcused and not excused:
            lines += [t["rep_all_came"], ""]
        if always:
            lines.append(t["rep_always_came"].format(n=len(always)))
            lines.append(", ".join(escape(w.name) for w in always))

    for year, month in stats.missing:
        lines += ["", t["rep_missing_tab"].format(tab=f"{month_name(lang, month)} {year}")]
    return "\n".join(lines).strip()


def _payroll_block(t: dict[str, str], report: MonthReport) -> str:
    what = t["pay_what_all"] if report.deduct_excused else t["pay_what_unexcused"]
    lines = [t["pay_title"], t["pay_note"].format(work=report.work_days, what=what),
             t["pay_sat_rule"], ""]
    total_salary = total_deduction = total_payable = 0
    for p in report.pays or []:
        name = escape(p.name)
        if p.salary is None:
            lines.append(t["pay_line_nosalary"].format(name=name))
            continue
        total_salary += p.salary
        total_deduction += p.deduction
        total_payable += p.payable
        if p.deduction:
            line = t["pay_line_full"].format(
                name=name, payable=fmt_money(p.payable), salary=fmt_money(p.salary),
                deduction=fmt_money(p.deduction), days=p.deduct_days)
        else:
            line = t["pay_line_none"].format(name=name, payable=fmt_money(p.payable))
        if p.compensated:
            line += t["pay_comp_note"].format(n=p.compensated)
        lines.append(line)
    lines += ["", t["pay_total"].format(salary=fmt_money(total_salary),
                                         deduction=fmt_money(total_deduction),
                                         payable=fmt_money(total_payable))]
    return "\n".join(lines)


def format_month(t: dict[str, str], lang: str, title: str, report: MonthReport) -> str:
    """Oylik davomat + maosh hisobi (boss tugmasi va 1-kundagi avtomatik hisobot uchun)."""
    text = format_period(t, lang, title, report.stats)
    if not report.tab_found:
        return text
    if report.pays is None:
        return text + "\n\n" + t["pay_no_cols"]
    return text + "\n\n" + _payroll_block(t, report)
