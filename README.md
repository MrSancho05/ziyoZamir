# Ziyo Zamir Bot

## Ishga tushirish
1. `credentials/` papkasiga Google service account faylini `ziyozamirbot.json` nomi bilan qo'ying.
2. `.env` faylida:
   - `BOT_TOKEN` ga yangi tokenni yozing (@BotFather orqali),
   - `ADMIN_IDS` ga administratorning Telegram ID sini yozing (@userinfobot dan bilib olinadi).
     Bir nechta admin bo'lsa vergul bilan: `111,222`.
3. Buyruqlar (alohida-alohida):

    pip install -r requirements.txt
    python main.py

## Rollar
| Imkoniyat | Admin | Boss | Sklad nazoratchisi | Ishchi |
|---|---|---|---|---|
| Tanqis ro'yxatni ko'rish | + | + | + | + |
| PDF / CSV yuklab olish | + | + | + | - |
| Foydalanuvchilarni boshqarish | + | - | - | - |

Ruxsatlar `app/core/permissions.py` da. Yangi imkoniyat qo'shish: shu faylda
`Permission` qo'shing va rollarga bering, keyin handlerga `HasPermission(...)` filtrini qo'ying.

## Kirish tartibi
Yangi odam botga yozadi -> adminga so'rov boradi -> admin rol tanlaydi.
Ma'lumotlar `data/bot.db` (SQLite) da saqlanadi. `ADMIN_IDS` dagi odamlar
bot har ishga tushganda avtomatik admin qilib tiklanadi.

## Serverda ishlatish
`DEPLOY.md` ga qarang.

## Yo'qlama
- Yo'qlamani admin va sklad nazoratchisi oladi ("📝 Yo'qlama"). Ishchi ustiga bosganda holat aylanadi:
  ⬜ -> ✅ keldi (`+`) -> ❌ sababsiz kelmadi (`-`) -> 🟡 sababli kelmadi (`S`).
  Jadvalga qo'lda yozsangiz ham shu belgilar ishlatiladi (`S` o'rniga kirill `С` ham bo'ladi).
- Shanba va yakshanba dam olish kuni: yo'qlama olinmaydi, hisobotlarda ham sanalmaydi.
- Har ish kuni 10:00 da (`REMINDER_HOUR`) admin va sklad nazoratchisiga eslatma boradi,
  agar bugungi yo'qlama hali olinmagan bo'lsa.
- Har YIL uchun bitta varaq (`ATTENDANCE_TAB_FORMAT`, standart `Davomat 2026`). Oylar shu varaqda
  bloklar bo'lib ketma-ket joylashadi: oy nomi qatori, kun raqamlari qatori (`Исм`, 1..31), ishchilar.
  Blok oy nomi bo'yicha topiladi. Yangi oyda birinchi yo'qlama olinganda bot oxirgi blokning nusxasini
  pastga qo'yadi (belgilar tozalanadi, kun raqamlari, oy nomi va oylik/to'lanadigan maosh yangilanadi).
  Yangi yilda yangi varaq ochiladi (oldingi yilning oxirgi bloki bilan). Eski oylar o'zgarmaydi.
- Maosh: sababli kelmagan kunlar ushlanmaydi (`DEDUCT_EXCUSED=1` bo'lsa ushlanadi).
- Haftalik hisobot: har yakshanba 09:00, oylik: oyning 1-kuni 09:00 (o'tgan oy uchun), bossga.
  Vaqt: `TIMEZONE` (standart Asia/Tashkent), soat: `REPORT_HOUR` (standart 9).

## Shanba va istalgan kunni o'zgartirish
- "📝 Yo'qlama" tugmasi ikkita variant beradi: "Bugungi yo'qlamani o'tkazish" va "Istalgan kun yo'qlamasini
  o'zgartirish" (oylik kalendar: ✅ olingan, ⚠️ olinmagan, 🛠 shanba, 🚫 oy bloki yo'q; ◀️ ▶️ oylar).
  O'tgan va kelgusi sanalarni ham tanlab to'g'rilash mumkin. Mavjud bo'lmagan oy bloki yaratilmaydi.
- Shanba - ixtiyoriy ish kuni: ro'yxatda hamma xodim chiqadi, faqat kelganlar belgilanadi (+).
  Kelmaganlarga ushlanma yo'q va jadvalga hech narsa yozilmaydi. Yakshanba - dam olish kuni (qizil),
  shanba jadvalda och sariq.
- Ishlangan har bir shanba shu oydagi kelmagan kunlardan 1 tasini kamaytiradi (nolgacha, qo'shimcha
  haq yo'q). Maosh ustuni (AH) har saqlashda qayta hisoblanadi.
- Jadvalga qo'lda `+` yozishda Google Sheets formula deb o'ylaydi: `'+` (oldiga apostrof) yozing.
