"""Boss/Admin tahlili uchun davrlar: kunlik, haftalik, oylik, mavsumiy, yillik.

Hamma davr "bugungacha" hisoblanadi (hali tugamagan davr bugun bilan yopiladi). Taqqoslash uchun
oldingi davr ham qaytariladi: u hozirgi davr bilan bir xil uzunlikda, shuning uchun taqqoslash adolatli
(masalan, noyabrning 1–15 kunlari oktyabrning 1–15 kunlari bilan solishtiriladi).
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta

TF_DAILY, TF_WEEKLY, TF_MONTHLY, TF_SEASONAL, TF_YEARLY = "d", "w", "m", "s", "y"
TIMEFRAMES = (TF_DAILY, TF_WEEKLY, TF_MONTHLY, TF_SEASONAL, TF_YEARLY)

WINTER, SPRING, SUMMER, AUTUMN = 0, 1, 2, 3
SEASONS = (WINTER, SPRING, SUMMER, AUTUMN)


@dataclass(frozen=True, slots=True)
class Range:
    tf: str
    start: date
    end: date
    prev_start: date
    prev_end: date
    season: int | None = None        # mavsumiy davrda: 0..3
    season_year: int | None = None   # mavsumiy davrda: mavsum boshlangan yil (qish: dekabr yili)


def season_bounds(season: int, year: int) -> tuple[date, date]:
    """Mavsumning to'liq chegaralari. Qish: `year` dekabridan keyingi yil fevraligacha."""
    if season == WINTER:
        return date(year, 12, 1), date(year + 1, 3, 1) - timedelta(days=1)
    first_month = (3, 6, 9)[season - 1]
    last_month = first_month + 2
    return date(year, first_month, 1), date(year, last_month, calendar.monthrange(year, last_month)[1])


def latest_season_year(season: int, today: date) -> int:
    """Shu mavsumning boshlangan eng oxirgi (kelajakda bo'lmagan) yili."""
    if season == WINTER:
        return today.year if today.month == 12 else today.year - 1
    start, _ = season_bounds(season, today.year)
    return today.year if today >= start else today.year - 1


def _previous_working_day(day: date) -> date:
    prev = day - timedelta(days=1)
    while prev.weekday() == 6:          # yakshanba dam olish kuni
        prev -= timedelta(days=1)
    return prev


def _safe_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def build_range(tf: str, today: date, season: int = WINTER) -> Range:
    if tf == TF_DAILY:
        prev = _previous_working_day(today)
        return Range(tf, today, today, prev, prev)

    if tf == TF_WEEKLY:
        start = today - timedelta(days=today.weekday())
        return Range(tf, start, today, start - timedelta(days=7), today - timedelta(days=7))

    if tf == TF_MONTHLY:
        start = today.replace(day=1)
        prev_last = start - timedelta(days=1)
        return Range(tf, start, today, prev_last.replace(day=1),
                     _safe_day(prev_last.year, prev_last.month, today.day))

    if tf == TF_SEASONAL:
        season = season if season in SEASONS else WINTER
        year = latest_season_year(season, today)
        start, full_end = season_bounds(season, year)
        end = min(full_end, today)
        prev_start, prev_full_end = season_bounds(season, year - 1)
        prev_end = min(prev_full_end, prev_start + (end - start))
        return Range(tf, start, end, prev_start, prev_end, season, year)

    # yillik
    return Range(TF_YEARLY, date(today.year, 1, 1), today, date(today.year - 1, 1, 1),
                 _safe_day(today.year - 1, today.month, today.day))


def shifted_range(tf: str, today: date, season: int = WINTER, offset: int = 0) -> Range:
    """`build_range` + davrlar bo'ylab yurish: offset=0 — joriy davr (bugungacha), -1 — oldingi TO'LIQ davr
    (o'tgan hafta / oy / mavsum / yil), -2 — undan oldingisi va h.k. Kelajak (offset > 0) bo'lmaydi."""
    if offset >= 0:
        return build_range(tf, today, season)
    if tf == TF_DAILY:
        day = today
        for _ in range(-offset):
            day = _previous_working_day(day)
        return build_range(TF_DAILY, day)
    if tf == TF_WEEKLY:
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        return build_range(TF_WEEKLY, monday + timedelta(days=6))
    if tf == TF_MONTHLY:
        idx = today.year * 12 + today.month - 1 + offset
        year, month = idx // 12, idx % 12 + 1
        return build_range(TF_MONTHLY, date(year, month, calendar.monthrange(year, month)[1]))
    if tf == TF_SEASONAL:
        season = season if season in SEASONS else WINTER
        year = latest_season_year(season, today) + offset
        return build_range(TF_SEASONAL, season_bounds(season, year)[1], season)
    return build_range(TF_YEARLY, date(today.year + offset, 12, 31))
