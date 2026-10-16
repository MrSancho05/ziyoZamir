from aiogram import Router

from app.handlers import admin, attendance, deficit, download, fallback, language, start

router = Router(name="root")
router.include_routers(
    start.router,
    language.router,
    admin.router,
    attendance.router,
    deficit.router,
    download.router,
    fallback.router,  # doim eng oxirida
)
