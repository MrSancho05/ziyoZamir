from aiogram import Router

from app.handlers import (
    admin,
    attendance,
    attendance_view,
    deficit,
    download,
    fallback,
    language,
    start,
)

router = Router(name="root")
router.include_routers(
    start.router,
    language.router,
    admin.router,
    attendance.router,
    attendance_view.router,
    deficit.router,
    download.router,
    fallback.router,  # doim eng oxirida
)
