import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import load_settings
from app.handlers import router
from app.middlewares.access import AccessMiddleware
from app.services.access import check_admins_reachable
from app.services.attendance import AttendanceService
from app.services.flows import FlowsService
from app.services.jobs import OpsScheduler
from app.services.scheduler import ReportScheduler
from app.services.reports.pdf import register_fonts
from app.services.shop import ShopService
from app.services.sheets import SheetsClient
from app.services.warehouse import WarehouseService
from app.services.web_server import DashboardService, WebServer, create_app, make_authorizer
from app.keyboards.webapp import set_webapp_url
from app.services.webapp_menu import sync_all_menu_buttons
from app.storage.attendance_store import AttendanceStore
from app.storage.categories import CategoryStore
from app.storage.norms import NormMemory
from app.storage.database import Database
from app.storage.memory import UserStore
from app.storage.flows_store import FlowStore
from app.storage.pending import PendingReports
from app.storage.report_cache import ReportCache
from app.storage.report_log import ReportLog
from app.storage.users import UserRepository


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    settings = load_settings()
    register_fonts(settings.fonts_dir)

    db = Database(settings.db_path)
    repo = UserRepository(db)
    await repo.ensure_admins(settings.admin_ids)

    sheets = SheetsClient(settings)
    store = UserStore()
    attendance = AttendanceService(sheets, settings.attendance_tab_format, settings.deduct_excused)
    report_log = ReportLog(db)
    pending = PendingReports(db)
    att_store = AttendanceStore(db)
    shop = ShopService(sheets, settings.shop_tab_format)
    flow_tabs = {"inflow": settings.inflow_tab, "outflow": settings.outflow_tab}
    flows = FlowsService(sheets, FlowStore(db), flow_tabs)
    warehouse = WarehouseService(sheets, inventory_tab=settings.worksheet_name, inflow_tab=settings.inflow_tab,
                                 outflow_tab=settings.outflow_tab, directory_tab=settings.directory_tab,
                                 tz=settings.timezone)
    warehouse.cat_store = CategoryStore(db)            # umumiy kategoriyalar (6 ta standart bilan)
    await warehouse.cat_store.ensure_seeded()
    warehouse.norm_store = NormMemory(db)              # oxirgi Мин. остаток tavsiyalari
    report_cache = ReportCache(db)
    try:
        # yetishmagan ustunlar jadval OXIRIGA qo'shiladi (o'rtaga hech qachon); qayta ishga tushirish xavfsiz
        await warehouse.migrate_schema()
    except Exception:
        logging.exception("Jadval ustunlarini tekshirib bo'lmadi (bot ishlashda davom etadi)")
    try:
        # BIR MARTALIK (data/.spravochnik_cleaned_v1): Справочник tozalanadi va standart birliklar yoziladi,
        # avtomatik import qilingan harakatsiz tovarlar Остатки dan olinadi. Avval data/ ga zaxira nusxa.
        # Mahsulotlar endi faqat Остатки и Расходы dan o'qiladi; Справочник dan avto-import YO'Q.
        await warehouse.reset_directory_once(settings.db_path.parent)
    except Exception:
        logging.exception("Справочник ni tozalab bo'lmadi (keyingi ishga tushishda qayta urinadi)")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await check_admins_reachable(bot, settings.admin_ids)

    # repo, store, sheets, settings handlerlarga nomi bo'yicha avtomatik uzatiladi
    dp = Dispatcher(repo=repo, store=store, sheets=sheets, settings=settings, attendance=attendance,
                    shop=shop, pending=pending, att_store=att_store, flows=flows,
                    warehouse=warehouse, report_cache=report_cache)

    # outer_middleware: handler topilmasa ham ishlaydi, shuning uchun
    # ruxsatsiz odam hech qaysi xabar bilan o'tib keta olmaydi
    access = AccessMiddleware(repo, store)       # store: 🎭 Rolni sinash
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)

    dp.include_router(router)

    logging.info("Bot ishga tushdi")
    # 📊 Jonli Dashboard tugmalari: hisobotlar yuborilishidan OLDIN yoqiladi
    if settings.webapp_url and not set_webapp_url(settings.webapp_url):
        logging.warning("WEBAPP_URL https:// bilan boshlanishi kerak (Telegram talabi): tugma ko'rsatilmaydi")
    scheduler = ReportScheduler(bot, repo, report_log, attendance, settings.timezone,
                                reminder_at=settings.attendance_reminder_at,
                                deadline_at=settings.attendance_deadline_at)
    scheduler_task = asyncio.create_task(scheduler.run())
    ops = OpsScheduler(
        bot=bot, repo=repo, log=report_log, pending=pending, attendance=attendance, shop=shop, flows=flows,
        tz=settings.timezone, report_hour=settings.report_hour,
        shop_reminder_at=settings.shop_reminder_at, shop_deadline_at=settings.shop_deadline_at,
        attendance_deadline_at=settings.attendance_deadline_at, attendance_send_at=settings.attendance_send_at,
        flows_report_at=settings.flows_report_at, activity_check_at=settings.activity_check_at,
        auto_send_minutes=settings.auto_send_minutes, flow_tabs=flow_tabs, att_store=att_store,
        warehouse=warehouse, report_cache=report_cache, flows_reminder_at=settings.flows_reminder_at,
        digest_at=settings.digest_at, summary_at=settings.summary_at)
    ops_task = asyncio.create_task(ops.run())

    # 📊 Jonli Dashboard: polling bilan bir jarayonda (aiohttp). Port band bo'lsa ham bot ishlayveradi
    dashboard = DashboardService(shop=shop, flows=flows, attendance=attendance, warehouse=warehouse,
                                 tz=settings.timezone)
    web_server = WebServer(create_app(dashboard, make_authorizer(settings.bot_token, repo)),
                           settings.webapp_host, settings.webapp_port)
    if set_webapp_url(settings.webapp_url):         # takroriy chaqiruv xavfsiz
        # chap pastki "Menu" tugmasi: Boss/Admin — 📊 Dashboard, boshqalar — oddiy menyu (fonda, startni kechiktirmaydi)
        menu_task = asyncio.create_task(sync_all_menu_buttons(bot, repo))
    else:
        menu_task = None
    try:
        await web_server.start()
    except OSError:
        logging.exception("Dashboard serverini %s:%d da ishga tushirib bo'lmadi", settings.webapp_host,
                          settings.webapp_port)
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        ops_task.cancel()
        if menu_task is not None:
            menu_task.cancel()
        await web_server.stop()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
