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
    warehouse,
)

router = Router(name="root")
router.include_routers(
    warehouse.router,   # Kirim / Chiqim / Mahsulotlar (admin + sklad); ochiq jarayonni menyu/buyruqda yopadi
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
