import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.keyboards.inline import attendance_start_kb
from app.locales.texts import get_texts, month_name
from app.services.attendance import AttendanceService
from app.services.attendance_grid import is_workday
from app.services.attendance_report import format_month, format_period
from app.services.boss_reports import send_to_bosses
from app.services.notify import deliver_once, staff_recipients
from app.storage.report_log import ReportLog
from app.storage.users import UserRepository
from app.utils.schedule import last_monthly_due, last_weekly_due

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=10)
REMINDER_UNTIL_HOUR = 18   # shundan keyin eslatma yuborilmaydi


class ReportScheduler:
    """Avtomatik vazifalar:
    - har ish kuni soat `reminder_hour` da BARCHA admin va sklad nazoratchilariga yo'qlama eslatmasi
      (agar bugungi yo'qlama hali olinmagan bo'lsa). Eslatma har bir qabul qiluvchi uchun alohida hisobda:
      kimgadir yetib bormasa, 18:00 gacha shu odamga qayta uriniladi;
    - har yakshanba soat `hour` da bossga (va uning nusxasi adminga) haftalik hisobot;
    - har oyning 1-kuni soat `hour` da bossga (va adminga) o'tgan oy hisoboti.

    Har daqiqa tekshiradi. Yuborilganlar bazada eslab qolinadi, shuning uchun bot o'sha
    soatda o'chiq bo'lsa ham, yoqilganda kechikib yuboradi va ikki marta yubormaydi."""

    def __init__(self, bot: Bot, repo: UserRepository, log: ReportLog,
                 attendance: AttendanceService, tz: ZoneInfo, hour: int,
                 reminder_hour: int = 10) -> None:
        self._bot = bot
        self._repo = repo
        self._log = log
        self._attendance = attendance
        self._tz = tz
        self._hour = hour
        self._reminder_hour = reminder_hour
        self._retry_at: dict[str, datetime] = {}

    async def run(self) -> None:
        logger.info("Rejalashtiruvchi ishga tushdi (hisobot %02d:00, eslatma %02d:00, %s)",
                    self._hour, self._reminder_hour, self._tz.key)
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("Rejalashtiruvchida xatolik")
            await asyncio.sleep(60)

    async def _tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self._tz)
        try:
            await self._reminder(now)
        except Exception:
            logger.exception("Yo'qlama eslatmasida xatolik")

        # Haftalik: yakshanba kuni, hafta dushanbadan yakshanbagacha
        due = last_weekly_due(now, self._hour)
        end = due.date()
        start = end - timedelta(days=6)
        await self._process(
            "weekly", end.isoformat(), now,
            fetch=lambda: self._attendance.period_stats(start, end),
            render=lambda t, lang, stats: format_period(
                t, lang,
                t["rep_weekly_title"].format(start=start.strftime("%d.%m"), end=end.strftime("%d.%m.%Y")),
                stats),
        )

        # Oylik: oyning 1-kuni, o'tgan oy uchun (maosh hisobi bilan)
        due_m = last_monthly_due(now, self._hour)
        last_day = (due_m - timedelta(days=1)).date()
        await self._process(
            "monthly", f"{last_day.year}-{last_day.month:02d}", now,
            fetch=lambda: self._attendance.month_payroll(last_day.year, last_day.month, last_day),
            render=lambda t, lang, report: format_month(
                t, lang,
                t["rep_monthly_title"].format(month=month_name(lang, last_day.month), year=last_day.year),
                report),
        )

    async def _reminder(self, now: datetime) -> None:
        if not is_workday(now.date()):
            return
        if not (self._reminder_hour <= now.hour < REMINDER_UNTIL_HOUR):
            return
        key = now.date().isoformat()
        if await self._log.was_sent("reminder", key):
            return
        if now < self._retry_at.get("reminder", now):
            return

        try:
            recorded = await self._attendance.is_day_recorded(now.date())
        except Exception:
            logger.exception("Eslatma: jadvalni tekshirib bo'lmadi, keyinroq qayta uriniladi")
            self._retry_at["reminder"] = now + RETRY_AFTER
            return

        if not recorded:
            recipients = await staff_recipients(self._repo)
            done = await deliver_once(
                self._bot, self._log, recipients, "reminder", key,
                build=lambda t, lang: t["att_reminder"].format(date=now.strftime("%d.%m.%Y")),
                markup=attendance_start_kb)
            if not done:
                # kimgadir yetib bormadi: faqat shu odamlarga keyinroq qayta uriniladi
                self._retry_at["reminder"] = now + RETRY_AFTER
                logger.warning("Yo'qlama eslatmasi hammaga yetib bormadi, %d daqiqadan keyin qayta uriniladi",
                               RETRY_AFTER.seconds // 60)
                return
            logger.info("Yo'qlama eslatmasi yuborildi: %d kishi", len(recipients))
        await self._log.mark_sent("reminder", key)

    async def _process(self, kind: str, key: str, now: datetime,
                       fetch: Callable[[], Awaitable[Any]],
                       render: Callable[[dict[str, str], str, Any], str]) -> None:
        if await self._log.was_sent(kind, key):
            return
        if not await self._log.has_any(kind):
            # Birinchi ishga tushish: eski davr hisobotini yubormaymiz, faqat belgilab qo'yamiz
            await self._log.mark_sent(kind, key)
            logger.info("%s: boshlang'ich belgi qo'yildi (%s)", kind, key)
            return
        if now < self._retry_at.get(kind, now):
            return

        try:
            data = await fetch()
        except Exception:
            logger.exception("%s hisobot ma'lumotini o'qib bo'lmadi, 10 daqiqadan keyin qayta uriniladi", kind)
            self._retry_at[kind] = now + RETRY_AFTER
            return

        delivered = await send_to_bosses(self._bot, self._repo, lambda t, lang: render(t, lang, data))
        if delivered == 0:
            logger.warning("%s hisobotni yuboradigan boss yo'q yoki hech biriga yetib bormadi", kind)

        await self._log.mark_sent(kind, key)
        logger.info("%s hisobot yuborildi (%s), bosslar: %d", kind, key, delivered)
