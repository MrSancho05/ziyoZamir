# Ziyo Zamir MCHJ — AI yordamchi (matn va 🎙 ovoz): Admin uchun yo'riqnoma

> **AI yordamchi FAQAT Admin uchun yoqilgan** — faol roli Admin bo'lganda. Boss, Sklad nazoratchisi, Usta va
> Ishchi uchun AI o'chiq: ular menyu tugmalari bilan ishlaydi, Gemini chaqirilmaydi (0 token).
> Admin **🎭 Rolni sinash** bilan boshqa rolga o'tsa — o'sha rolning menyusi ko'rinadi va AI ham o'chadi;
> **🔙 Adminga qaytish** yoki `/admin` bosilgach AI yana ishlaydi.

Botga **matn** yozing yoki **🎙 ovozli xabar** yuboring — AI tushunadi, ma'lumotni Google Sheets dan oladi va
kerak bo'lsa yozuv tayyorlaydi.

## Asosiy qoidalar

- **Hech narsa o'zi yozilmaydi.** Har bir kirim, chiqim, tahrirlash yoki o'chirish uchun **karta** chiqadi:
  **✅ Tasdiqlash** — jadvalga yoziladi, **❌ Bekor qilish** — hech narsa o'zgarmaydi. Karta 10 daqiqa amal qiladi
  va faqat uni so'ragan admin tasdiqlay oladi.
- **Faqat ishga oid savollar.** Boshqa mavzularga bitta jumla:
  *«⚠️ Men faqat Ziyo Zamir MCHJ ombori, xarajatlari va davomati bo'yicha ishga oid savollarga javob beraman.»*
- **Bir xabarda bir nechta amal** — har biri uchun alohida karta (bir navbatda 6 tagacha bosqich).
- Sana aytilmasa — **bugun**; «kecha», «o'tgan hafta», «shu oy», «25-noyabr» tushuniladi.
- Qisqa summa: **450 = 450 000 so'm** (do'kon va boshqa xarajatlarda).
- **Yordam:** «Yordam», «Nima qila olaman?» — admin imkoniyatlari 4–6 qatorda. `/help` ham shuni ko'rsatadi.
- **Til:** «Tilni rus tiliga o'zgartir», «O'zbek tiliga o'tkaz» (matn yoki ovoz) yoki `/lang`.

## 📥 Kirim (Приход)

| Misol | Izoh |
|---|---|
| «Omborga 20 ta petlya 4500 so'mdan kirim qil, Alkan Groupdan» | Bitta karta |
| «3 karobka samorez keldi, karobkasi 90 ming, Rustam do'kon» | Karobka → dona avtomatik |
| «10 ta akfix kirim qil, Bozordan» | Narx aytilmasa — oxirgi narx 🔁 |
| «5 ta skotch kirim, narxi keyin» | Narxsiz (sariq), keyin 💰 narx |
| «20 ta petlya va 2 kg samorez kirim qil, Alkan» | 2 ta karta |

## 📤 Chiqim (Расход)

| Misol | Izoh |
|---|---|
| «5 ta samorez Olimjon ustaga chiqim qil» | Qoldiq tekshiriladi |
| «Shavkatga 2 ta akfix, sex uchun» | Izoh bilan |
| «Omborga 20 ta petlya 4500 dan kirim qil va 5 ta samorez Olimjon ustaga chiqim qil» | Zanjir: 2 karta |

## ✏️ Bugungi va o'tgan yozuvlarni tahrirlash / 🗑 o'chirish

| Misol | Nima bo'ladi |
|---|---|
| «Kechagi akfix kirimini o'chir» | Kechagi akfix kirimlari topiladi; bir nechta bo'lsa — ro'yxat, qaysi biri so'raladi |
| «Kecha kiritilgan akfix kirimini o'chirib, bugunga 15 ta yangidan kirim qil» | 2 karta: o'chirish + yangi kirim |
| «25-noyabrdagi petlya kirimi 20 emas, 25 ta edi» | Miqdor tahrirlanadi |
| «Bugungi skotch kirimining narxi 12 ming» | Narx yoziladi, narxsiz ro'yxatdan chiqadi |
| «Olimjonga bergan samorez 5 emas 3 ta edi» | Chiqim miqdori tahrirlanadi |
| «Kechagi Shavkat chiqimini o'chir» | Chiqim qatori o'chiriladi |
| «Kechagi benzin xarajatini 350 ga o'zgartir» | 🧾 summa 350 000 |
| «Bugungi suv xarajatini o'chir» | 🧾 qator o'chiriladi |
| «Kechagi do'kon xarajatini 520 qil» | 🏪 kun summasi |
| «24-noyabr do'kon xarajatini o'chir» | 🏪 kun tozalanadi (yana sariq — kiritilmagan) |

O'chirilgan qator jadvaldan butunlay olib tashlanadi (Table1 / Table2 ichida bo'sh qator qolmaydi), qoldiq,
o'rtacha narx va status formulalar bilan qayta hisoblanadi, narxsiz kirimlar ro'yxati moslanadi. **Bossga
yuborilgan do'kon kuni** o'zgartirilsa yoki o'chirilsa — AI **sabab** so'raydi va Boss / Adminga ✏️ tuzatish xabari
boradi. Har bir o'zgarish audit jurnaliga yoziladi; Jonli Dashboard, hisobotlar va qoldiq so'rovlari darhol yangi
raqamlarni ko'rsatadi.

Menyu orqali ham (AI siz, Admin va Sklad nazoratchisi): **📦 Mahsulotlar → 🗂 Yozuvlarni tahrirlash**.

## 📦 Katalog, narx, ustalar, birliklar

| Misol | Izoh |
|---|---|
| «Yangi mahsulot: Petlya 35mm, dona, minimal 50 ta» | Kategoriya avtomatik, Мин. остаток > 0 majburiy |
| «Kromka Noch koma 1/19 qo'sh, minimal 2 rulon» | 🎞 Кромка ПВХ, рулон |
| «Petlya 35mm nomini Петля накладная 35мм ga o'zgartir» | 4 ta varaqda |
| «Ruchka 224 mahsulotini o'chir» | Остатки va Справочник dan |
| «Akfix qoldig'iga 5 ta qo'sh» / «Akfixdan 3 tani ayir» | ⚖️ Qoldiqni to'g'rilash |
| «Skotch kirimiga narx qo'y: 12000» | 💰 Narxi yo'q tovar |
| «Ustalar ro'yxatiga Bekzodni qo'sh» / «Bekzodni ustalardan o'chir» | 👷 Ustalar |
| «O'lchov birligi «litr» qo'sh» | 📏 Birliklar |

## 🏪 Do'kon va 🧾 Boshqa xarajatlar

| Misol | Izoh |
|---|---|
| «Bugungi do'kon xarajati 450» | 450 000 so'm |
| «Kechagi do'kon xarajati 380 ming edi» | O'tgan kun |
| «Damas benzin 120 ming» | 🧾 Топливо / Бензин |
| «Stanok ta'miri 300, kecha» | 🧾 Ремонт |

## 📊 Tahlil va savollar

| Savol | Javob |
|---|---|
| «Omborda akfix qancha qoldi?» | Qoldiq, Мин. остаток, o'rtacha narx |
| «Qaysi mahsulotlar tanqis?» | Tanqis tovarlar |
| «Ombor qiymati kategoriyalar bo'yicha» | So'm, kategoriyalar bo'yicha |
| «Narxi kiritilmagan kirimlar bormi?» | Sana, mahsulot, miqdor |
| «Shu hafta qanday yangi mahsulotlar qo'shildi?» | 🆕 yangi mahsulotlar |
| «Kecha omborga qancha kirim, qancha chiqim bo'ldi?» | Kirim / chiqim summalari |
| «Shu oy do'konga qancha ketdi?» / «O'tgan oy boshqa xarajatlar?» | 🏪 / 🧾 |
| «Bu hafta kim necha kun kelmadi?» / «Noyabr maoshi qancha?» | Davomat / maosh |

## Boshqa rollar (AI siz)

| Rol | Nima qiladi |
|---|---|
| 📈 Boss | 📊 Qoldiqlar, 🔎 Davomat tahlili, 📈 Xarajat tahlili, hisobotlar; mahsulot nomini yozsa — qoldig'i |
| 📦 Sklad nazoratchisi | 📥 Kirim, 📤 Chiqim, 📦 Mahsulotlar (🗂 yozuvlarni tahrirlash ham), 📝 Yo'qlama, 🏪, 🧾 — tugmalar orqali |
| 👷 Usta | 📊 Qoldiqlar va 📤 Chiqim — tugmalar orqali (narxsiz) |
| 🧑‍🔧 Ishchi | 📅 Mening davomatim, 📊 Qoldiqlar |

Hamma rollar uchun til: «Tilni rus tiliga o'zgartir» deb **yozish** (API siz) yoki `/lang`; `/help` — rolga mos
qisqa yo'riqnoma.

## Sozlamalar (.env)

| O'zgaruvchi | Standart | Izoh |
|---|---|---|
| `GEMINI_API_KEY` | — | https://aistudio.google.com/apikey (kalit bo'lmasa AI o'chiq) |
| `GEMINI_PRIMARY_MODEL` | `gemini-3.5-flash` | `GEMINI_MODEL` berilgan bo'lsa — o'sha |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` | Asosiy model 429 / 503 / 404 bersa — darhol shu model |
