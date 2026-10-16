from datetime import date
from html import escape

from app.locales.texts import month_name
from app.services.attendance_grid import PeriodStats


def format_daily(t: dict[str, str], day: date, came: list[str], unexcused: list[str],
                 excused: list[str], by_name: str) -> str:
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
        "",
        t["rep_by"].format(name=escape(by_name)),
    ]
    return "\n".join(lines)


def format_period(t: dict[str, str], lang: str, title: str, stats: PeriodStats) -> str:
    lines = [title, ""]

    if stats.recorded_days == 0:
        lines.append(t["rep_no_data"])
    else:
        lines += [
            t["rep_recorded_days"].format(work=stats.work_days, n=stats.recorded_days),
            t["rep_legend"],
            "",
        ]
        for w in stats.workers:
            line = f"• {escape(w.name)} — ✅ {w.came} | 🟡 {w.excused} | ❌ {w.absent}"
            if w.unmarked:
                line += f" | ⬜ {w.unmarked}"
            lines.append(line)

    for year, month in stats.missing:
        lines += ["", t["rep_missing_tab"].format(tab=f"{month_name(lang, month)} {year}")]
    return "\n".join(lines)
