from aiogram import Router

from app.handlers import (
    admin,
    attendance,
    analytics,
    deficit,
    download,
    fallback,
    language,
    my_attendance,
    shop,
    start,
    top_products,
)

router = Router(name="root")
router.include_routers(
    start.router,
    language.router,
    admin.router,
    attendance.router,
    analytics.router,
    shop.router,
    my_attendance.router,
    top_products.router,
    deficit.router,
    download.router,
    fallback.router,  # doim eng oxirida
)
