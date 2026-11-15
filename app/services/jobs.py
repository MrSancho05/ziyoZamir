import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.core.permissions import Role
from app.keyboards.inline import attendance_start_kb, shop_enter_kb
from app.locales.texts import get_texts, month_name
from app.services.access import safe_send
from app.services.attendance import AttendanceService
from app.services.attendance_grid import is_markable_day
from app.services.boss_reports import (
    attendance_day_text,
    format_flows_daily,
    format_flows_period,
    format_shop_daily,
    format_shop_period,
    send_to_bosses,
)
from app.services.flows import FlowDelta, FlowsService
from app.services.notify import deliver_once, staff_recipients
from app.services.shop import ShopService
from app.services.worker_notify import notify_workers
from app.storage.attendance_store import AttendanceStore
from app.storage.pending import PendingReport, PendingReports
from app.storage.report_log import ReportLog
from app.storage.users import UserRepository
from app.utils.schedule import Period, periods

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=10)
_CMP_LABEL = {"weekly": "cmp_label_week", "monthly": "cmp_label_month", "yearly": "cmp_label_year"}


class OpsScheduler:
    """Kundalik nazorat va avtomatik hisobotlar:

    - 30 daqiqalik qoida: tayyor do'kon xarajati hisoboti bossga yuborilmasa, o'zi yuboradi;
    - yo'qlama: belgilangan vaqtgacha (`attendance_send_at`) bossga yuborilmagan bo'lsa, bot uni kuniga
      BIR marta yuboradi. Yuborilgandan keyingi o'zgarishlar qayta yuborilmaydi;
    - do'kon xarajati: 10:00 eslatma, 11:30 gacha kiritilmasa bossga ogohlantirish (yakshanba dam olish);
    - yo'qlama 14:30 gacha olinmasa bossga ogohlantirish (dushanba-juma);
    - hamma eslatma va muddat o'tgani haqidagi xabarlar BARCHA admin va sklad nazoratchilariga boradi
      (har bir qabul qiluvchi uchun alohida hisobda: yetib bormaganiga qayta uriniladi);
    - bossga boradigan HAR BIR xabarning nusxasi adminga ham yuboriladi (`send_to_bosses`);
    - 18:30: kunlik kirim/chiqim hisoboti, 19:00: kun faolligi tekshiruvi;
    - haftalik / oylik / yillik hisobotlar (do'kon xarajati, kirim/chiqim) oldingi davr bilan taqqoslanadi.

    Har daqiqa tekshiradi. Bajarilganlar bazada (ReportLog) belgilanadi: qayta ishga tushirishda
    ikki marta yuborilmaydi, kechikkan vazifa ish vaqti ichida keyinroq bajariladi."""

    def __init__(self, *, bot: Bot, repo: UserRepository, log: ReportLog, pending: PendingReports,
                 attendance: AttendanceService, shop: ShopService, flows: FlowsService, tz: ZoneInfo,
                 report_hour: int, shop_reminder_at: time, shop_deadline_at: time,
                 attendance_deadline_at: time, attendance_send_at: time, flows_report_at: time,
                 activity_check_at: time, auto_send_minutes: int, flow_tabs: dict[str, str],
                 att_store: AttendanceStore) -> None:
        self._bot, self._repo, self._log, self._pending = bot, repo, log, pending
        self._attendance, self._shop, self._flows = attendance, shop, flows
        self._tz, self._hour = tz, report_hour
        self._shop_reminder_at, self._shop_deadline_at = shop_reminder_at, shop_deadline_at
        self._att_deadline_at, self._flows_at, self._activity_at = attendance_deadline_at, flows_report_at, activity_check_at
        self._att_send_at = attendance_send_at
        self._att_store = att_store
        self._auto_minutes = auto_send_minutes
        self._tabs = flow_tabs
        self._retry_at: dict[str, datetime] = {}

    async def run(self) -> None:
        logger.info("Nazorat rejalashtiruvchisi ishga tushdi (%s)", self._tz.key)
        while True:
            await self._tick()
            await asyncio.sleep(60)

    async def _tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self._tz)
        # Eslatma va muddat xabarlari birinchi turadi: keyingi (sekin) Google Sheets vazifalari ularni kechiktirmasin
        for job in (self._shop_reminder, self._shop_deadline, self._attendance_deadline,
                    self._auto_send, self._attendance_auto_send,
                    self._flows_daily, self._activity_check, self._shop_reports, self._flows_reports):
            try:
                await job(now)
            except Exception:
                logger.exception("Vazifa bajarilmadi: %s", job.__name__)

    # ---------------- umumiy yordamchilar ----------------
    async def _daily(self, kind: str, now: datetime, start: time, job: Callable[[], Awaitable[Any]],
                     end: time | None = None) -> None:
        """Vazifa kuniga bir marta: `start` dan keyin (va `end` gacha). job False qaytarsa, qayta uriniladi."""
        clock = now.time()
        if clock < start or (end is not None and clock >= end):
            return
        key = now.date().isoformat()
        if await self._log.was_sent(kind, key) or now < self._retry_at.get(kind, now):
            return
        try:
            done = await job()
        except Exception:
            logger.exception("%s bajarilmadi, 10 daqiqadan keyin qayta uriniladi", kind)
            self._retry_at[kind] = now + RETRY_AFTER
            return
        if done is not False:
            await self._log.mark_sent(kind, key)

    async def _staff(self) -> list:
        """Mas'ul xodimlar: barcha admin va barcha sklad nazoratchilari."""
        return await staff_recipients(self._repo)

    async def _alert_bosses(self, key: str, day: date) -> int:
        return await send_to_bosses(self._bot, self._repo,
                                    lambda t, lang: t[key].format(date=day.strftime("%d.%m.%Y")))

    # ---------------- 30 daqiqalik avto-yuborish ----------------
    async def _auto_send(self, now: datetime) -> None:
        cutoff = int(now.timestamp()) - self._auto_minutes * 60
        for item in await self._pending.due(cutoff, kinds=("shop",)):
            try:
                delivered = await self._send_pending(item)
            except Exception:
                logger.exception("Avto-yuborish xatosi: %s %s", item.kind, item.key)
                continue
            if not delivered:
                logger.warning("Avto-yuborish: %s %s hech kimga yetib bormadi", item.kind, item.key)
            await self._pending.mark_sent(item.kind, item.key, int(now.timestamp()), auto=True)
            if delivered:
                await self._notify_auto_sent(item)

    async def _send_pending(self, item: PendingReport) -> int:
        day = date.fromisoformat(item.key)
        if item.kind == "shop":
            found = await self._shop.get_day(day)
            if not found or not found[0] or found[1] is None:
                return 0
            amount, total = found[1], await self._shop.month_total_until(day)
            return await send_to_bosses(self._bot, self._repo,
                                        lambda t, lang: format_shop_daily(t, day, amount, total))
        return 0

    async def _notify_auto_sent(self, item: PendingReport) -> None:
        """Avto-yuborilgani haqida xabar: saqlagan xodimga va barcha mas'ul xodimlarga (sklad nazoratchisi
        ham xabardor bo'ladi). Takrorlanmaydi."""
        users = list(await self._staff())
        saver = await self._repo.get(item.user_id) if item.user_id else None
        if saver is not None and saver.is_active and all(u.id != saver.id for u in users):
            users.append(saver)
        day = date.fromisoformat(item.key).strftime("%d.%m.%Y")
        for user in users:
            t = get_texts(user.lang)
            await safe_send(self._bot, user.id,
                            t["auto_sent_notice"].format(minutes=self._auto_minutes, what=t["auto_what_shop"],
                                                         date=day))

    # ---------------- yo'qlama: belgilangan vaqtda BIR martalik avto-yuborish ----------------
    async def _attendance_auto_send(self, now: datetime) -> None:
        """Mas'ul xodim yo'qlamani bossga yubormagan bo'lsa, belgilangan vaqtda kuniga bir marta yuboradi.
        Qo'lda yoki avtomatik yuborilgan bo'lsa, qayta yubormaydi. Keyingi tahrirlar ham yuborilmaydi."""
        async def job() -> bool:
            day = now.date()
            if not is_markable_day(day):                       # yakshanba
                return True
            data = await self._attendance.peek_day(day)
            if data is None or all(m is None for m in data[1]):
                return True                                     # yo'qlama olinmagan: 14:30 ogohlantirishi bor
            names, marks = data
            async with self._att_store.send_lock:
                if await self._att_store.is_sent(day):
                    return True
                delivered = await send_to_bosses(
                    self._bot, self._repo, lambda t, lang: attendance_day_text(t, day, names, marks, None))
                if delivered == 0:
                    logger.warning("Yo'qlama avto-yuborish: hech bir bossga yetib bormadi, qayta uriniladi")
                    self._retry_at["att_auto_send"] = now + RETRY_AFTER
                    return False
                await self._att_store.mark_sent(day, int(now.timestamp()), auto=True)
            logger.info("Yo'qlama avtomatik yuborildi (%s), bosslar: %d", day.isoformat(), delivered)

            for user in await self._staff():
                t = get_texts(user.lang)
                await safe_send(self._bot, user.id, t["att_auto_sent_notice"].format(
                    date=day.strftime("%d.%m.%Y"), time=self._att_send_at.strftime("%H:%M")))
            await notify_workers(self._bot, self._repo, self._att_store, day, names, marks)
            return True
        await self._daily("att_auto_send", now, self._att_send_at, job)

    # ---------------- do'kon xarajati: eslatma va ogohlantirish ----------------
    async def _shop_reminder(self, now: datetime) -> None:
        async def job() -> bool:
            today = now.date()
            if today.weekday() == 6 or await self._shop.is_entered(today):
                return True
            return await deliver_once(
                self._bot, self._log, await self._staff(), "shop_reminder_to", today.isoformat(),
                build=lambda t, lang: t["shop_reminder"].format(
                    date=today.strftime("%d.%m.%Y"), deadline=self._shop_deadline_at.strftime("%H:%M")),
                markup=shop_enter_kb)
        await self._daily("shop_reminder", now, self._shop_reminder_at, job, end=self._shop_deadline_at)

    async def _shop_deadline(self, now: datetime) -> None:
        async def job() -> bool:
            today = now.date()
            if today.weekday() == 6 or await self._shop.is_entered(today):
                return True
            key = today.isoformat()
            if not await self._log.was_sent("shop_deadline_boss", key):
                await self._alert_bosses("alert_shop_missing", today)
                await self._log.mark_sent("shop_deadline_boss", key)       # qayta urinishda bossga takror bormasin
            return await deliver_once(
                self._bot, self._log, await self._staff(), "shop_overdue_to", key,
                build=lambda t, lang: t["staff_shop_overdue"].format(
                    date=today.strftime("%d.%m.%Y"), deadline=self._shop_deadline_at.strftime("%H:%M")),
                markup=shop_enter_kb)
        await self._daily("shop_deadline", now, self._shop_deadline_at, job)

    # ---------------- yo'qlama: 14:30 ogohlantirish ----------------
    async def _attendance_deadline(self, now: datetime) -> None:
        async def job() -> bool:
            today = now.date()
            if today.weekday() >= 5 or await self._attendance.is_day_recorded(today):   # shanba ixtiyoriy
                return True
            key = today.isoformat()
            if not await self._log.was_sent("att_deadline_boss", key):
                await self._alert_bosses("alert_att_missing", today)
                await self._log.mark_sent("att_deadline_boss", key)        # qayta urinishda bossga takror bormasin
            return await deliver_once(
                self._bot, self._log, await self._staff(), "att_overdue_to", key,
                build=lambda t, lang: t["staff_att_overdue"].format(
                    date=today.strftime("%d.%m.%Y"), deadline=self._att_deadline_at.strftime("%H:%M")),
                markup=attendance_start_kb)
        await self._daily("att_deadline", now, self._att_deadline_at, job)

    # ---------------- kirim / chiqim ----------------
    async def _flows_daily(self, now: datetime) -> None:
        async def job() -> bool:
            if now.weekday() == 6:
                return True
            deltas = await self._flows.preview(now.date())
            await self._warn_missing_sheets(deltas)
            if not any(d.baseline for d in deltas.values() if d.sheet_found):
                return True                       # birinchi ishga tushish: nusxa 19:00 da olinadi
            if any(d.new_rows for d in deltas.values()):
                await send_to_bosses(self._bot, self._repo,
                                     lambda t, lang: format_flows_daily(t, now.date(), deltas, self._tabs))
            return True
        await self._daily("flows_daily", now, self._flows_at, job, end=self._activity_at)

    async def _activity_check(self, now: datetime) -> None:
        async def job() -> bool:
            today = now.date()
            if today.weekday() == 6:
                return True
            deltas = await self._flows.commit(today)           # kunni yopadi
            found = [d for d in deltas.values() if d.sheet_found]
            if not found or not any(d.baseline for d in found):
                return True
            if not any(d.changed for d in found):
                await self._alert_bosses("alert_flows_none", today)
            elif (not await self._log.was_sent("flows_daily", today.isoformat())
                  and any(d.new_rows for d in found)):
                # 18:30 hisoboti yuborilmagan bo'lsa (bot o'chiq bo'lgan), hozir yuboriladi
                await send_to_bosses(self._bot, self._repo,
                                     lambda t, lang: format_flows_daily(t, today, deltas, self._tabs))
                await self._log.mark_sent("flows_daily", today.isoformat())
            return True
        await self._daily("activity", now, self._activity_at, job)

    async def _warn_missing_sheets(self, deltas: dict[str, FlowDelta]) -> None:
        missing = [self._tabs[k] for k, d in deltas.items() if not d.sheet_found]
        if not missing:
            return
        for admin in await self._repo.list_by_role(Role.ADMIN):
            t = get_texts(admin.lang)
            await safe_send(self._bot, admin.id, t["alert_flows_columns"].format(
                problem=t["rep_flow_sheet_missing"].format(tab=", ".join(missing))))

    # ---------------- davriy hisobotlar ----------------
    async def _once_period(self, kind: str, key: str, now: datetime, fetch: Callable[[], Awaitable[Any]],
                           render: Callable[[dict[str, str], str, Any], str]) -> None:
        if await self._log.was_sent(kind, key):
            return
        if not await self._log.has_any(kind):
            await self._log.mark_sent(kind, key)               # birinchi ishga tushish: eski hisobotni yubormaymiz
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
        await self._log.mark_sent(kind, key)
        logger.info("%s hisobot yuborildi (%s), bosslar: %d", kind, key, delivered)

    @staticmethod
    def _title(t: dict[str, str], lang: str, p: Period, prefix: str) -> str:
        if p.kind == "weekly":
            return t[f"{prefix}_weekly_title"].format(start=p.start.strftime("%d.%m"), end=p.end.strftime("%d.%m.%Y"))
        if p.kind == "monthly":
            return t[f"{prefix}_monthly_title"].format(month=month_name(lang, p.end.month), year=p.end.year)
        return t[f"{prefix}_yearly_title"].format(year=p.end.year)

    async def _shop_reports(self, now: datetime) -> None:
        for p in periods(now, self._hour):
            async def fetch(p: Period = p):
                return (await self._shop.period_stats(p.start, p.end),
                        await self._shop.period_stats(p.prev_start, p.prev_end))

            def render(t: dict[str, str], lang: str, data: Any, p: Period = p) -> str:
                return format_shop_period(t, lang, self._title(t, lang, p, "rep_shop"), data[0], data[1], p.kind)

            await self._once_period(f"shop_{p.kind}", p.key, now, fetch, render)

    async def _flows_reports(self, now: datetime) -> None:
        for p in periods(now, self._hour):
            async def fetch(p: Period = p):
                return (await self._flows.summary(p.start, p.end),
                        await self._flows.summary(p.prev_start, p.prev_end))

            def render(t: dict[str, str], lang: str, data: Any, p: Period = p) -> str:
                return format_flows_period(t, lang, self._title(t, lang, p, "rep_flow"), data[0], data[1],
                                           _CMP_LABEL[p.kind])

            await self._once_period(f"flows_{p.kind}", p.key, now, fetch, render)
