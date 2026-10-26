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
from app.services.scheduler import ReportScheduler
from app.services.reports.pdf import register_fonts
from app.services.sheets import SheetsClient
from app.storage.database import Database
from app.storage.memory import UserStore
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
    attendance = AttendanceService(sheets, settings.attendance_tab_format)
    report_log = ReportLog(db)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await check_admins_reachable(bot, settings.admin_ids)

    # repo, store, sheets, settings handlerlarga nomi bo'yicha avtomatik uzatiladi
    dp = Dispatcher(repo=repo, store=store, sheets=sheets, settings=settings, attendance=attendance)

    # outer_middleware: handler topilmasa ham ishlaydi, shuning uchun
    # ruxsatsiz odam hech qaysi xabar bilan o'tib keta olmaydi
    access = AccessMiddleware(repo)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)

    dp.include_router(router)

    logging.info("Bot ishga tushdi")
    scheduler = ReportScheduler(bot, repo, report_log, attendance,
                                settings.timezone, settings.report_hour,
                                settings.reminder_hour)
    scheduler_task = asyncio.create_task(scheduler.run())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

