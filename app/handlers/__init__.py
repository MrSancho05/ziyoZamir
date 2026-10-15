from aiogram import Router

from app.handlers import admin, deficit, download, fallback, language, start

router = Router(name="root")
router.include_routers(
    start.router,
    language.router,
    admin.router,
    deficit.router,
    download.router,
    fallback.router,  # doim eng oxirida
)
