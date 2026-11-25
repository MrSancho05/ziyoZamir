import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.core.permissions import Role
from app.keyboards.inline import attendance_start_kb, digest_kb, report_toggle_kb, shop_enter_kb
from app.keyboards.warehouse import flows_reminder_kb, unpriced_reminder_kb
from app.locales.texts import TEXTS, get_texts, month_name
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
from app.services.attendance_grid import EXCUSED, MINUS, PLUS
from app.services.digest import (
    MONTHLY,
    WEEKLY,
    DigestData,
    ExecDay,
    pdf_lines,
    render_digest,
    render_exec_summary,
    render_flows_daily,
    unpriced_boss_text,
    unpriced_reminder_text,
)
from app.services.flows import FlowDelta, FlowsService
from app.services.notify import deliver_once, staff_recipients
from app.services.reports.pdf import build_text_pdf
from app.services.shop import ShopService
from app.services.warehouse import WarehouseService
from app.services.worker_notify import notify_workers
from app.storage.attendance_store import AttendanceStore
from app.storage.pending import PendingReport, PendingReports
from app.storage.report_cache import ReportCache
from app.storage.report_log import ReportLog
from app.storage.users import UserRepository
from app.utils.schedule import Period, last_month_end_due, last_saturday_due, periods

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
    - 17:30: kirim/chiqim kiritilmagan yoki narxi yo'q tovar bo'lsa — sklad xodimlariga eslatma (tugmalar bilan);
    - 18:30: kunlik kirim/chiqim hisoboti (📄 Batafsil) yoki "🚨 bugun hech narsa kiritilmadi" ogohlantirishi;
    - 19:00: kun yopiladi (flows_store);
    - 20:00 (dushanba–shanba): kunlik ijroiya xulosasi — do'kon + davomat + ombor (📄 Batafsil, 📊 Dashboard);
    - shanba 20:30: BITTA haftalik yig'ma hisobot (do'kon + ombor + davomat), oy oxiri 20:30: oylik (+ PDF);
    - yillik hisobotlar (do'kon xarajati, kirim/chiqim) oldingi yil bilan taqqoslanadi;
    - yakshanba: hech qanday eslatma, muddat yoki "kiritilmagan" ogohlantirishi yuborilmaydi.

    Har daqiqa tekshiradi. Bajarilganlar bazada (ReportLog) belgilanadi: qayta ishga tushirishda
    ikki marta yuborilmaydi, kechikkan vazifa ish vaqti ichida keyinroq bajariladi."""

    def __init__(self, *, bot: Bot, repo: UserRepository, log: ReportLog, pending: PendingReports,
                 attendance: AttendanceService, shop: ShopService, flows: FlowsService, tz: ZoneInfo,
                 report_hour: int, shop_reminder_at: time, shop_deadline_at: time,
                 attendance_deadline_at: time, attendance_send_at: time, flows_report_at: time,
                 activity_check_at: time, auto_send_minutes: int, flow_tabs: dict[str, str],
                 att_store: AttendanceStore, warehouse: WarehouseService, report_cache: ReportCache,
                 flows_reminder_at: time, digest_at: time, summary_at: time = time(20, 0),
                 unpriced_first_minutes: int = 60, unpriced_warn_at: time = time(11, 0),
                 unpriced_deadline_at: time = time(12, 0)) -> None:
        self._bot, self._repo, self._log, self._pending = bot, repo, log, pending
        self._attendance, self._shop, self._flows = attendance, shop, flows
        self._tz, self._hour = tz, report_hour
        self._shop_reminder_at, self._shop_deadline_at = shop_reminder_at, shop_deadline_at
        self._att_deadline_at, self._flows_at, self._activity_at = attendance_deadline_at, flows_report_at, activity_check_at
        self._att_send_at = attendance_send_at
        self._att_store = att_store
        self._auto_minutes = auto_send_minutes
        self._tabs = flow_tabs
        self._wh = warehouse
        self._cache = report_cache
        self._flows_rem_at = flows_reminder_at
        self._digest_at = digest_at
        self._summary_at = summary_at
        self._unp_first = timedelta(minutes=unpriced_first_minutes)
        self._unp_warn_at, self._unp_deadline_at = unpriced_warn_at, unpriced_deadline_at
        self._unp_synced: datetime | None = None
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
                    self._auto_send, self._attendance_auto_send, self._flows_reminder, self._unpriced_watch,
                    self._flows_daily, self._activity_check, self._exec_summary, self._digests,
                    self._shop_reports,
                    self._flows_reports):
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
    async def _flows_reminder(self, now: datetime) -> None:
        """17:30 (dushanba–shanba): bugun kirim/chiqim yo'q YOKI narxi kiritilmagan tovar bo'lsa — eslatma.
        Hammasi joyida bo'lsa, ortiqcha xabar yuborilmaydi."""
        async def job() -> bool:
            today = now.date()
            if today.weekday() == 6:
                return True
            n_in, n_out = await self._wh.day_counts(today)
            unpriced = len(await self._wh.unpriced())
            if n_in + n_out > 0 and unpriced == 0:
                return True
            deadline = self._flows_at.strftime("%H:%M")

            def build(t: dict[str, str], lang: str) -> str:
                text = t["flows_reminder"].format(deadline=deadline)
                if unpriced:
                    text += t["flows_reminder_unpriced"].format(count=unpriced)
                return text
            return await deliver_once(self._bot, self._log, await self._staff(), "flows_reminder_to",
                                      today.isoformat(), build=build,
                                      markup=lambda t: flows_reminder_kb(t, unpriced))
        await self._daily("flows_reminder", now, self._flows_rem_at, job, end=self._flows_at)

    async def _send_flows_daily(self, day: date, deltas: dict[str, FlowDelta]) -> int:
        """Kunlik kirim/chiqim: qisqacha + 📄 Batafsil (bitta xabar, joyida almashtiriladi)."""
        try:
            unpriced = [r for r in await self._wh.unpriced() if r.day == day]
        except Exception:
            logger.exception("Narxi yo'q tovarlarni o'qib bo'lmadi")
            unpriced = []
        key = day.isoformat()
        sections = {}
        for lang in TEXTS:
            t = get_texts(lang)
            sections[lang] = render_flows_daily(t, day, deltas, format_flows_daily(t, day, deltas, self._tabs),
                                                unpriced)
            await self._cache.save("fd", key, lang, sections[lang])
        return await send_to_bosses(self._bot, self._repo, lambda t, lang: sections[lang]["brief"],
                                    markup=lambda t, lang: report_toggle_kb(t, "fd", key))

    async def _flows_daily(self, now: datetime) -> None:
        async def job() -> bool:
            today = now.date()
            if today.weekday() == 6:
                return True
            deltas = await self._flows.preview(today)
            await self._warn_missing_sheets(deltas)
            found = [d for d in deltas.values() if d.sheet_found]
            if not found or not any(d.baseline for d in found):
                return True                       # birinchi ishga tushish: nusxa 19:00 da olinadi
            has_rows = any(d.new_rows for d in found)
            if not has_rows:
                try:
                    has_rows = sum(await self._wh.day_counts(today)) > 0
                except Exception:
                    logger.exception("Kunlik yozuvlarni sanab bo'lmadi")
            if has_rows:
                await self._send_flows_daily(today, deltas)
                return True
            key = today.isoformat()
            if not await self._log.was_sent("flows_none_alert", key):
                at = self._flows_at.strftime("%H:%M")
                await send_to_bosses(self._bot, self._repo, lambda t, lang: t["fd_none_alert"].format(
                    time=at, date=today.strftime("%d.%m.%Y")))
                await self._log.mark_sent("flows_none_alert", key)       # 19:00 da takrorlanmasin
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
            key = today.isoformat()
            if not any(d.changed for d in found):
                if not await self._log.was_sent("flows_none_alert", key):      # 18:30 da yuborilmagan bo'lsa
                    await self._alert_bosses("alert_flows_none", today)
                    await self._log.mark_sent("flows_none_alert", key)
            elif (not await self._log.was_sent("flows_daily", key)
                  and any(d.new_rows for d in found)):
                # 18:30 hisoboti yuborilmagan bo'lsa (bot o'chiq bo'lgan), hozir yuboriladi
                await self._send_flows_daily(today, deltas)
                await self._log.mark_sent("flows_daily", key)
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

    # ---------------- 📋 20:00 kunlik ijroiya xulosasi (dushanba–shanba) ----------------
    async def _exec_summary(self, now: datetime) -> None:
        async def job() -> bool:
            today = now.date()
            if today.weekday() == 6:                       # yakshanba — dam olish
                return True
            data = await self._collect_exec_day(today)
            if len(data.errors) >= 3:
                raise RuntimeError("ijroiya xulosasi: hech bir bo'limni o'qib bo'lmadi")   # 10 daqiqadan keyin qayta
            key = today.isoformat()
            sections = {}
            for lang in TEXTS:
                sections[lang] = render_exec_summary(get_texts(lang), lang, data)
                await self._cache.save("es", key, lang, sections[lang])
            delivered = await send_to_bosses(self._bot, self._repo, lambda t, lang: sections[lang]["brief"],
                                             markup=lambda t, lang: report_toggle_kb(t, "es", key))
            logger.info("Ijroiya xulosasi yuborildi (%s), bosslar: %d", key, delivered)
            return True
        await self._daily("exec_summary", now, self._summary_at, job, end=time(23, 0))

    async def _collect_exec_day(self, day: date) -> ExecDay:
        d = ExecDay(day=day)
        try:
            found = await self._shop.get_day(day)
            d.shop_entered = bool(found and found[0] and found[1] is not None)
            d.shop_amount = found[1] if d.shop_entered else None
            d.shop_month = await self._shop.month_total_until(day)
        except Exception:
            logger.exception("Ijroiya xulosasi: do'kon xarajatini o'qib bo'lmadi")
            d.errors.append("shop")
        try:
            peek = await self._attendance.peek_day(day)
            names, marks = peek if peek else ([], [])
            d.att_recorded = any(m is not None for m in marks)
            for name, mark in zip(names, marks):
                if mark == PLUS:
                    d.came.append(name)
                elif mark == MINUS:
                    d.absent.append(name)
                elif mark == EXCUSED:
                    d.excused.append(name)
                else:
                    d.unmarked += 1
        except Exception:
            logger.exception("Ijroiya xulosasi: davomatni o'qib bo'lmadi")
            d.errors.append("att")
        try:
            d.deltas = await self._flows.preview(day)
        except Exception:
            logger.exception("Ijroiya xulosasi: kirim/chiqimni o'qib bo'lmadi")
            d.errors.append("flows")
        try:
            unpriced = await self._wh.unpriced()
            d.unpriced_total = len(unpriced)
            d.unpriced_today = [r for r in unpriced if r.day == day]
            d.stock_value = await self._wh.stock_value()
            d.low = await self._wh.low_items()
        except Exception:
            logger.exception("Ijroiya xulosasi: ombor qoldig'ini o'qib bo'lmadi")
            d.errors.append("stock")
        await self._collect_extras(d, day, day)
        return d

    # ---------------- 🧾 / 🆕 / 📦 kategoriya / ⚠️ narxsiz — hisobotlar uchun qo'shimcha ma'lumot ----------------
    async def _collect_extras(self, d: Any, start: date, end: date, pstart: date | None = None,
                              pend: date | None = None) -> None:
        """Kunlik xulosa (ExecDay) va yig'ma hisobot (DigestData) uchun umumiy bo'limlar. Har biri alohida:
        bittasi o'qilmasa, qolganlari baribir chiqadi."""
        other = getattr(self._wh, "other", None)
        if other is not None:
            try:
                d.other = await other.summary(start, end)
                if pstart is not None:
                    d.other_prev = await other.summary(pstart, pend)
            except Exception:
                logger.exception("Hisobot: «Прочие расходы» ni o'qib bo'lmadi")
        log = getattr(self._wh, "product_log", None)
        if log is not None:
            try:
                d.new_products = await log.between(start, end)
            except Exception:
                logger.exception("Hisobot: yangi mahsulotlar jurnalini o'qib bo'lmadi")
        try:
            d.cat_values = await self._wh.category_values(await self._flows.rows("outflow", start, end))
        except Exception:
            logger.exception("Hisobot: kategoriyalar bo'yicha qiymatni hisoblab bo'lmadi")
        store = getattr(self._wh, "unpriced_store", None)
        if store is not None:
            try:
                today = datetime.now(self._tz).date().isoformat()
                # Boss nazorati: o'tgan kunlardan qolgan (12:00 muddati o'tgan) narxsiz kirimlar
                d.unpriced_ctl = [it for it in await store.open_items() if it.entry_date < today]
            except Exception:
                logger.exception("Hisobot: narxsiz kirimlar nazoratini o'qib bo'lmadi")

    # ---------------- 💰 narxsiz kirimlar: 1-eslatma, 11:00 ogohlantirish, 12:00 Boss nazorati ----------------
    async def _unpriced_watch(self, now: datetime) -> None:
        store = getattr(self._wh, "unpriced_store", None)
        if store is None or now.weekday() == 6:              # yakshanba — dam olish
            return
        if self._unp_synced is None or now - self._unp_synced >= timedelta(minutes=10):
            # haqiqiy manba — Приход varag'i (qo'lda yozilgan narxlar ham hisobga olinadi)
            try:
                rows = await self._wh.unpriced()
                today = now.date().isoformat()
                await store.reconcile([(r.row, r.name, r.qty, r.unit, r.day.isoformat() if r.day else today)
                                       for r in rows], int(now.timestamp()))
                self._unp_synced = now
            except Exception:
                logger.exception("Narxsiz kirimlarni Приход bilan moslab bo'lmadi")
                return
        items = await store.open_items()
        if not items:
            return
        today = now.date()
        ts = int(now.timestamp())
        if time(8, 0) <= now.time() < time(20, 0):
            due = [it for it in items if it.first_reminded is None and ts - it.created_at >= self._unp_first.total_seconds()]
            if due:
                await self._send_unpriced(items, today, "unp_first")
                await store.mark([it.id for it in due], "first_reminded", ts)
        key = today.isoformat()
        if self._unp_warn_at <= now.time() < self._unp_deadline_at and not await self._log.was_sent("unp_warn", key):
            late = [it for it in items if it.entry_date < key or it.first_reminded is not None]
            if late:
                await self._send_unpriced(items, today, "unp_warn")
                await store.mark([it.id for it in late], "second_reminded", ts)
            await self._log.mark_sent("unp_warn", key)
        if now.time() >= self._unp_deadline_at and not await self._log.was_sent("unp_escalate", key):
            overdue = [it for it in items if it.entry_date < key]
            if overdue:
                await store.mark([it.id for it in overdue if it.escalated_at is None], "escalated_at", ts)
                await send_to_bosses(self._bot, self._repo,
                                     lambda t, lang: unpriced_boss_text(t, overdue, today))
            await self._log.mark_sent("unp_escalate", key)

    async def _send_unpriced(self, items: list, today: date, kind: str) -> None:
        """Sklad nazoratchilariga (bo'lmasa — adminlarga): bugungi va o'tgan kunlardan qolganlar alohida."""
        users = await self._repo.list_by_role(Role.STOREKEEPER) or await self._repo.list_by_role(Role.ADMIN)
        for user in users:
            t = get_texts(user.lang)
            await safe_send(self._bot, user.id, unpriced_reminder_text(t, items, today, strict=kind == "unp_warn",
                                                                      deadline=self._unp_deadline_at),
                            reply_markup=unpriced_reminder_kb(t, len(items)))


    # ---------------- 📊 yig'ma hisobotlar: shanba 20:30 va oy oxiri 20:30 ----------------
    async def _digests(self, now: datetime) -> None:
        w_due = last_saturday_due(now, self._digest_at).date()           # shanba
        w_start = w_due - timedelta(days=5)                                # dushanba
        m_due = last_month_end_due(now, self._digest_at).date()          # oyning oxirgi kuni
        m_start = m_due.replace(day=1)
        m_prev_end = m_start - timedelta(days=1)
        for kind, key, start, end, pstart, pend in (
                (WEEKLY, w_due.isoformat(), w_start, w_due, w_start - timedelta(days=7), w_due - timedelta(days=7)),
                (MONTHLY, f"{m_due.year}-{m_due.month:02d}", m_start, m_due, m_prev_end.replace(day=1), m_prev_end)):
            log_kind = f"digest_{kind}"
            if await self._log.was_sent(log_kind, key):
                continue
            if not await self._log.has_any(log_kind):
                await self._log.mark_sent(log_kind, key)       # birinchi ishga tushish: eski davr yuborilmaydi
                logger.info("%s: boshlang'ich belgi qo'yildi (%s)", log_kind, key)
                continue
            if now < self._retry_at.get(log_kind, now):
                continue
            data = await self._collect_digest(kind, start, end, pstart, pend)
            if len(data.errors) == 3:
                logger.warning("%s: hech bir bo'limni o'qib bo'lmadi, 10 daqiqadan keyin qayta uriniladi", log_kind)
                self._retry_at[log_kind] = now + RETRY_AFTER
                continue
            await self._send_digest(kind, key, data)
            await self._log.mark_sent(log_kind, key)

    async def _collect_digest(self, kind: str, start: date, end: date, pstart: date, pend: date) -> DigestData:
        d = DigestData(kind=kind, start=start, end=end, prev_start=pstart, prev_end=pend)
        try:
            d.shop = await self._shop.period_stats(start, end)
            d.shop_prev = await self._shop.period_stats(pstart, pend)
        except Exception:
            logger.exception("Yig'ma hisobot: do'kon xarajatini o'qib bo'lmadi")
            d.errors.append("shop")
        try:
            d.flows = await self._flows.summary(start, end)
            d.stock_value = await self._wh.stock_value()
            d.low = await self._wh.low_items()
        except Exception:
            logger.exception("Yig'ma hisobot: ombor ma'lumotini o'qib bo'lmadi")
            d.errors.append("wh")
        try:
            d.att = await self._attendance.period_stats(start, end)
        except Exception:
            logger.exception("Yig'ma hisobot: davomatni o'qib bo'lmadi")
            d.errors.append("att")
        await self._collect_extras(d, start, end, pstart, pend)
        return d

    async def _send_digest(self, kind: str, key: str, data: DigestData) -> int:
        sections = {}
        for lang in TEXTS:
            sections[lang] = render_digest(get_texts(lang), lang, data)
            await self._cache.save(kind, key, lang, sections[lang])

        def monthly_pdf(t: dict[str, str], lang: str):
            title = t["dg_pdf_title"].format(month=month_name(lang, data.end.month), year=data.end.year)
            try:
                pdf = build_text_pdf(title, pdf_lines(t, lang, data), self._tz)
            except Exception:
                logger.exception("Oylik PDF ni yasab bo'lmadi")
                return None
            return pdf, f"hisobot_{key}.pdf", title

        document = monthly_pdf if kind == MONTHLY else None
        delivered = await send_to_bosses(self._bot, self._repo, lambda t, lang: sections[lang]["brief"],
                                         markup=lambda t, lang: digest_kb(t, kind, key), document=document)
        logger.info("Yig'ma hisobot yuborildi (%s %s), bosslar: %d", kind, key, delivered)
        return delivered

    async def _shop_reports(self, now: datetime) -> None:
        # haftalik va oylik — yig'ma hisobot ichida; alohida faqat yillik
        for p in [x for x in periods(now, self._hour) if x.kind == "yearly"]:
            async def fetch(p: Period = p):
                return (await self._shop.period_stats(p.start, p.end),
                        await self._shop.period_stats(p.prev_start, p.prev_end))

            def render(t: dict[str, str], lang: str, data: Any, p: Period = p) -> str:
                return format_shop_period(t, lang, self._title(t, lang, p, "rep_shop"), data[0], data[1], p.kind)

            await self._once_period(f"shop_{p.kind}", p.key, now, fetch, render)

    async def _flows_reports(self, now: datetime) -> None:
        for p in [x for x in periods(now, self._hour) if x.kind == "yearly"]:
            async def fetch(p: Period = p):
                return (await self._flows.summary(p.start, p.end),
                        await self._flows.summary(p.prev_start, p.prev_end))

            def render(t: dict[str, str], lang: str, data: Any, p: Period = p) -> str:
                return format_flows_period(t, lang, self._title(t, lang, p, "rep_flow"), data[0], data[1],
                                           _CMP_LABEL[p.kind])

            await self._once_period(f"flows_{p.kind}", p.key, now, fetch, render)
