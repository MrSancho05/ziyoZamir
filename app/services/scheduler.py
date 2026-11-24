import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.keyboards.inline import attendance_start_kb
from app.services.attendance import AttendanceService
from app.services.attendance_grid import is_workday
from app.services.notify import deliver_once, staff_recipients
from app.storage.report_log import ReportLog
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=10)


class ReportScheduler:
    """Yo'qlama eslatmasi: har ish kuni (dushanba–juma; yakshanba dam olish) soat `reminder_at` da (13:30)
    BARCHA admin va sklad nazoratchilariga, agar bugungi yo'qlama hali olinmagan bo'lsa. Xabarda Bossga
    hisobot ketadigan muddat (`deadline_at`, 14:30) aniq yoziladi. Eslatma muddatgacha yuboriladi; kimgadir
    yetib bormasa, faqat shu odamga qayta uriniladi.

    Haftalik va oylik hisobotlar endi bitta yig'ma hisobot sifatida OpsScheduler da (shanba / oy oxiri 20:30)."""

    def __init__(self, bot: Bot, repo: UserRepository, log: ReportLog, attendance: AttendanceService,
                 tz: ZoneInfo, reminder_at: time, deadline_at: time) -> None:
        self._bot = bot
        self._repo = repo
        self._log = log
        self._attendance = attendance
        self._tz = tz
        self._reminder_at = reminder_at
        self._deadline_at = deadline_at
        self._retry_at: dict[str, datetime] = {}

    async def run(self) -> None:
        logger.info("Rejalashtiruvchi ishga tushdi (yo'qlama eslatmasi %s, muddat %s, %s)",
                    self._reminder_at.strftime("%H:%M"), self._deadline_at.strftime("%H:%M"), self._tz.key)
        while True:
            try:
                await self._reminder(datetime.now(self._tz))
            except Exception:
                logger.exception("Yo'qlama eslatmasida xatolik")
            await asyncio.sleep(60)

    async def _reminder(self, now: datetime) -> None:
        if now.weekday() == 6 or not is_workday(now.date()):       # yakshanba — dam olish
            return
        if not (self._reminder_at <= now.time() < self._deadline_at):
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
                build=lambda t, lang: t["att_reminder"].format(date=now.strftime("%d.%m.%Y"),
                                                               deadline=self._deadline_at.strftime("%H:%M")),
                markup=attendance_start_kb)
            if not done:
                self._retry_at["reminder"] = now + RETRY_AFTER
                logger.warning("Yo'qlama eslatmasi hammaga yetib bormadi, %d daqiqadan keyin qayta uriniladi",
                               RETRY_AFTER.seconds // 60)
                return
            logger.info("Yo'qlama eslatmasi yuborildi: %d kishi", len(recipients))
        await self._log.mark_sent("reminder", key)
