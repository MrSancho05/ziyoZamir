from dataclasses import dataclass
from datetime import date, datetime, timedelta


def last_weekly_due(now: datetime, hour: int) -> datetime:
    """Eng oxirgi o'tgan yakshanba soat `hour`:00 (hozirgi vaqtdan oshmagan)."""
    days_since_sunday = (now.weekday() + 1) % 7
    due = (now - timedelta(days=days_since_sunday)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    if due > now:
        due -= timedelta(days=7)
    return due


def last_monthly_due(now: datetime, hour: int) -> datetime:
    """Eng oxirgi o'tgan oyning 1-kuni soat `hour`:00 (hozirgi vaqtdan oshmagan)."""
    due = now.replace(day=1, hour=hour, minute=0, second=0, microsecond=0)
    if due > now:
        due = (due - timedelta(days=1)).replace(day=1)
    return due


def last_yearly_due(now: datetime, hour: int) -> datetime:
    """Eng oxirgi o'tgan 1-yanvar soat `hour`:00."""
    due = now.replace(month=1, day=1, hour=hour, minute=0, second=0, microsecond=0)
    if due > now:
        due = due.replace(year=due.year - 1)
    return due


@dataclass(frozen=True, slots=True)
class Period:
    kind: str          # weekly | monthly | yearly
    key: str           # takroran yuborilmasligi uchun davr kaliti
    start: date
    end: date
    prev_start: date   # taqqoslash uchun oldingi davr
    prev_end: date


def periods(now: datetime, hour: int) -> list[Period]:
    """Hozirgi vaqtga ko'ra yuborilishi kerak bo'lgan oxirgi tugagan hafta / oy / yil."""
    end = last_weekly_due(now, hour).date()                      # yakshanba
    start = end - timedelta(days=6)
    weekly = Period("weekly", end.isoformat(), start, end, start - timedelta(days=7), end - timedelta(days=7))

    last_day = (last_monthly_due(now, hour) - timedelta(days=1)).date()
    first = last_day.replace(day=1)
    prev_last = first - timedelta(days=1)
    monthly = Period("monthly", f"{last_day.year}-{last_day.month:02d}", first, last_day,
                     prev_last.replace(day=1), prev_last)

    year = last_yearly_due(now, hour).year - 1
    yearly = Period("yearly", str(year), date(year, 1, 1), date(year, 12, 31),
                    date(year - 1, 1, 1), date(year - 1, 12, 31))
    return [weekly, monthly, yearly]
