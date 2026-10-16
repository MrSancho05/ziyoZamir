from datetime import datetime, timedelta


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
