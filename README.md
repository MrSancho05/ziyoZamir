# Ziyo Zamir Bot

## Ishga tushirish
1. `credentials/` papkasiga Google service account faylini `ziyozamirbot.json` nomi bilan qo'ying.
2. `.env` faylida:
   - `BOT_TOKEN` ga yangi tokenni yozing (@BotFather orqali),
   - `ADMIN_IDS` ga administratorning Telegram ID sini yozing (@userinfobot dan bilib olinadi).
     Bir nechta admin bo'lsa vergul bilan: `111,222`.
   - 🤖 AI yordamchi (ixtiyoriy, bepul): `GEMINI_API_KEY` — https://aistudio.google.com/apikey dan olinadi;
     `GEMINI_MODEL` (standart `gemini-2.5-flash-lite`). Kalit bo'lmasa yordamchi o'chiq, bot avvalgidek ishlaydi.
     Faol jarayon bo'lmaganda savol, ovozli xabar va rasmga javob beradi; ma'lumotlarni faqat o'qiydi.
3. Buyruqlar (alohida-alohida):

    pip install -r requirements.txt
    python main.py

## Rollar
| Imkoniyat | Admin | Boss | Sklad nazoratchisi | Ishchi |
|---|---|---|---|---|
| Mahsulotlarni tekshirish (barcha / tanqis ro'yxatni ko'rish) | + | + | + | + |
| PDF / CSV yuklab olish (barcha va tanqis mahsulotlar) | + | + | + | - |
| Foydalanuvchilarni boshqarish | + | - | - | - |
| Davomatni va do'kon xarajatlarini tekshirish (davrlar bo'yicha tahlil) | + | + | - | - |
| 🏆 Top mahsulotlar (`VIEW_TOP_PRODUCTS`) | + | - | - | - |

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

## Do'kon xarajatlari, kirim/chiqim va nazorat
- **Do'kon xarajati** ("🏪 Do'kon xarajati", admin va sklad nazoratchisi): bugungi yoki istalgan kunning summasi
  yoziladi (kalendar orqali o'tgan/kelgusi kunlarni ham o'zgartirish mumkin). Jadval: yillik varaq
  `Магазин 2026` (`SHOP_TAB_FORMAT`), oylar blok bo'lib ketma-ket joylashadi (oy nomi, `День | Цена`, kunlar, `Итог:`).
  Yakshanba jadvalda avtomatik qizil bo'yaladi, xarajat kiritilmaydi. Yangi oy/yilda bot blok/varaqni o'zi yaratadi.
- **10:00** eslatma; **11:30** gacha kiritilmasa bossga ogohlantirish.
- **30 daqiqalik qoida**: yo'qlama yoki do'kon xarajati saqlangach, xodim "Bossga yuborish" ni bosmasa, 30 daqiqadan
  keyin bot hisobotni bossga o'zi yuboradi (taymer qayta ishga tushsa ham saqlanadi; tahrirlansa qaytadan boshlanadi).
- **Yo'qlama 14:30** gacha olinmasa (dushanba-juma) bossga ogohlantirish.
- **Kirim/chiqim** (`Приход`, `Расход`): **18:30** da kunlik hisobot, **19:00** da kun faolligi tekshiriladi:
  ikkala varaqda ham bugungi yozuv bo'lmasa bossga ogohlantirish. Bossga FAQAT joriy kunning yozuvlari boradi:
  varaqda sana ustuni (`Дата` / `Sana` / `Date`) bo'lsa, faqat bugungi sanali qatorlar olinadi, o'tgan sanalardagi
  o'zgarishlar hisobga olinmaydi (sanasi bo'sh qator yuqoridagi sanani oladi). Sana ustuni bo'lmasa, kun boshidagi
  nusxa bilan solishtiriladi (birinchi ishga tushirishda faqat nusxa olinadi). Faqat sanasi bor, mahsulot nomi /
  miqdori / summasi yo'q (to'liq bo'lmagan) qatorlar e'tiborga olinmaydi.
- **Haftalik / oylik / yillik** hisobotlar (do'kon xarajati va kirim/chiqim) bossga avtomatik boradi va oldingi
  davr bilan taqqoslanadi (qancha ko'p/kam).
- **Ishchi** ("🙋 Mening davomatim"): faqat o'z kelgan/kelmagan kunlari va maoshi. Admin ishchini
  yo'qlama jadvalidagi ism bilan "Foydalanuvchilar -> ishchi -> Jadvaldagi ismni bog'lash" orqali bog'laydi.
- Vaqtlar `.env` orqali o'zgartiriladi (`.env.example` ga qarang).

## Mahsulotlarni tekshirish va PDF
- "📊 Mahsulotlarni tekshirish" tugmasi ikkita ro'yxat beradi: "Barcha mahsulotlar" va "Tanqis mahsulotlar".
  Ro'yxatni (sahifalab) hamma rol ko'ra oladi. PDF / CSV yuklab olish tugmasi faqat `DOWNLOAD_REPORTS` ruxsati
  borlarda (admin, boss, sklad nazoratchisi) chiqadi; ishchi faqat matnli ro'yxatni ko'radi.
- PDF dagi sana/vaqt server vaqti bilan emas, `TIMEZONE` (standart `Asia/Tashkent`, UTC+5) bo'yicha yoziladi.

## Boss va Admin tahlili (davomat va do'kon xarajatlari)
- "🔎 Davomatni tekshirish" va "📈 Do'kon xarajatlarini tekshirish" (ruxsatlar: `VIEW_ATTENDANCE`, `VIEW_EXPENSES`;
  admin va boss). Ikkalasida ham 5 ta davr: **Kunlik, Haftalik, Oylik, Mavsumiy** (Qish: dekabr–fevral, Bahor: mart–may,
  Yoz: iyun–avgust, Kuz: sentyabr–noyabr) va **Yillik**. Hamma davr bugungacha hisoblanadi; haftalik — shu haftaning
  dushanbasidan, mavsumiy — tanlangan mavsumning eng oxirgi davri.
- Natija avval **qisqacha**: do'kon xarajatida faqat umumiy summa (UZS), davomatda egasi uchun asosiy xulosa
  (formula va foizsiz). Xabar ostidagi **"Batafsil"** tugmasi xabarni o'sha joyida (yangi xabar yubormasdan) to'liq
  tahlilga almashtiradi, tugma **"Qisqacha"** ga o'zgaradi; uni bosish qisqa ko'rinishni qaytaradi.
- Batafsil ko'rinish: oldingi davr bilan taqqoslash (oldingi davr bir xil uzunlikda olinadi: oy — oldingi oyning shu
  kunlari, mavsum — o'tgan yilning shu mavsumi, yil — oldingi yilning shu sanagacha), foizlar, ishchilar bo'yicha
  kelmagan sanalar va oddiy tilda qadamma-qadam hisoblash formulalari. Oylik davomatda maosh hisobi ham bor.
- Ma'lumot yo'q yoki yetarli bo'lmasa (yoki yakshanba/dam olish kuni) aniq xabar ko'rsatiladi.
- Batafsil ko'rinish bitta xabarga sig'ishi uchun Telegram chegarasi (4096 belgi) hisobga olingan: uzun sanalar
  ro'yxati avtomatik qisqartiriladi. Natija 5 daqiqa keshlanadi (tugmani almashtirishda jadval qayta o'qilmaydi).

## Umumiy qoidalar: eslatmalar, Admin nusxasi, Top mahsulotlar
- **Eslatmalar hamma mas'ullarga**: yo'qlama (10:00) va do'kon xarajati (10:00) eslatmalari, shuningdek muddat o'tgani
  haqidagi xabarlar (do'kon 11:30, yo'qlama 14:30) BARCHA adminlarga va BARCHA sklad nazoratchilariga boradi.
  Har bir qabul qiluvchi uchun yetkazish alohida hisobda (`report_log`, `<tur>@<id>` kaliti): bittasiga yetib
  bormasa (tarmoq, Telegram limiti) qolganlarga xalaqit bermaydi va faqat shu odamga 10 daqiqada qayta uriniladi.
  Bot bloklangan bo'lsa qayta urinilmaydi. 30 daqiqalik avto-yuborish haqidagi xabar ham sklad nazoratchisiga boradi.
- **Admin nusxasi**: bossga boradigan HAR BIR xabar (avtomatik va qo'lda: yo'qlama, do'kon xarajati, kirim/chiqim,
  haftalik/oylik/yillik hisobotlar, ogohlantirishlar) adminlarga ham yuboriladi. Admin boss ro'yxatida bo'lsa, ikki marta
  yuborilmaydi. Nusxa boss(lar)ga yetib borgandan keyin yuboriladi (qayta urinishda takror bo'lmasligi uchun).
- **🏆 Top mahsulotlar** (faqat admin): `Приход` (to'ldirish chastotasi) va `Расход` (chiqim hajmi/chastotasi)
  varaqlaridan har bir mahsulot uchun ball hisoblanadi (chiqim miqdori 40%, chiqim chastotasi 30%, kirim chastotasi 30%;
  log-shkala), mahsulotlar ABC/velocity bo'yicha tartiblanadi: A (yuqori 20%) -> B (30%) -> C (50%) -> D (chiqimi yo'q).
  Har sinf uchun omborda joylashtirish tavsiyasi beriladi (A — ustaxona/chiqish eshigiga eng yaqin, bel balandligida).
  Davrlar: oxirgi 30 kun, 90 kun, barcha vaqt (varaqda sana ustuni bo'lsa; bo'lmasa barcha vaqt hisoblanadi).
  ⚠️ — tez aylanadi, lekin kirim yo'q; 📦 — kirim chiqimdan ancha ko'p (ortiqcha zaxira).
