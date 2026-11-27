from aiogram import Router

from app.handlers import (
    admin,
    ai_confirm,
    attendance,
    analytics,
    deficit,
    download,
    fallback,
    language,
    my_attendance,
    other_expenses,
    reports,
    roles,
    shop,
    start,
    top_products,
    warehouse,
)

router = Router(name="root")
router.include_routers(
    roles.router,       # 🎭 Rolni sinash / 🔙 Adminga qaytish / ❓ Yordam
    reports.router,     # Boss/Admin hisobotlari: Batafsil <-> Qisqacha (joyida tahrirlash)
    warehouse.router,
    other_expenses.router,   # 🧾 Boshqa xarajatlar (sklad + admin)   # Kirim / Chiqim / Mahsulotlar (admin + sklad); ochiq jarayonni menyu/buyruqda yopadi
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
    ai_confirm.router,  # 🤖 AI qoralamalari: ✅ Tasdiqlash / ❌ Bekor qilish
    fallback.router,  # doim eng oxirida
)
