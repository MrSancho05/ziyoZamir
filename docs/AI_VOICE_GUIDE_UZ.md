# Ziyo Zamir MCHJ — AI yordamchi: o'zbek tilidagi foydalanish yo'riqnomasi

Botga **oddiy matn** yozing yoki **🎙 ovozli xabar** yuboring — AI yordamchi tushunadi, ombor va xarajatlar
ma'lumotini Google Sheets dan oladi va kerak bo'lsa yozuv tayyorlaydi.

## Asosiy qoidalar

- **Hech narsa o'zi yozilmaydi.** Har bir kirim, chiqim, tahrirlash yoki o'chirish uchun bot **karta** chiqaradi:
  **✅ Tasdiqlash** — jadvalga yoziladi, **❌ Bekor qilish** — hech narsa o'zgarmaydi.
  Kartani faqat uni so'ragan odam tasdiqlay oladi, karta **10 daqiqa** amal qiladi.
- **Faqat ishga oid savollar.** Ob-havo, yangiliklar, hazil va boshqa mavzularga bot bitta jumla bilan javob beradi:
  *«⚠️ Men faqat Ziyo Zamir MCHJ ombori, xarajatlari va davomati bo'yicha ishga oid savollarga javob beraman.»*
- **Bir xabarda bir nechta amal** mumkin — har biri uchun alohida karta chiqadi.
- Sana aytilmasa — **bugun**. «Kecha», «o'tgan hafta», «shu oy», «25-noyabr» kabi so'zlarni tushunadi.
- Qisqa summa: **450 = 450 000 so'm** (do'kon va boshqa xarajatlarda).
- Tugmalar ham ishlayveradi: 📥 Kirim, 📤 Chiqim, 📦 Mahsulotlar, 🏪 Do'kon xarajati, 🧾 Boshqa xarajatlar.

## 🌐 Tilni o'zgartirish (hamma uchun)

| Nima deyish kerak | Natija |
|---|---|
| «Tilni rus tiliga o'zgartir» | Bot ruschaga o'tadi, menyu yangilanadi |
| «O'zbek tiliga o'tkaz» | Bot o'zbekchaga qaytadi |
| «Смени язык на русский» / «Переключи на узбекский» | Xuddi shunday |
| `/lang` | Til tanlash tugmalari |

## ❓ Yordam: «Nima qila olaman?»

Yozing yoki ayting: **«Yordam»**, **«Nima qila olaman?»**, **«Qanday ishlataman?»** — bot faqat **sizning
rolingizga** ruxsat etilgan amallarni 4–6 qatorda aytadi. `/help` buyrug'i ham shuni ko'rsatadi.

---

## 📈 Boss (faqat ko'rish va tahlil)

Boss hech narsani yozmaydi va o'chirmaydi — kirim / chiqim / xarajatlarni Admin va Sklad nazoratchisi kiritadi.

| Savol (matn yoki ovoz) | Javob |
|---|---|
| «Omborda akfix qancha qoldi?» | Qoldiq, Мин. остаток, o'rtacha narx |
| «Qaysi mahsulotlar tanqis?» | Tanqis tovarlar ro'yxati |
| «Ombor qiymati qancha? Kategoriyalar bo'yicha» | Jami va kategoriyalar bo'yicha so'm |
| «Narxi kiritilmagan kirimlar bormi?» | Sana, mahsulot, miqdor |
| «Shu hafta qanday yangi mahsulotlar qo'shildi?» | 🆕 yangi mahsulotlar |
| «Bugun / shu oy do'konga qancha ketdi?» | 🏪 do'kon xarajati |
| «O'tgan oy boshqa xarajatlar qancha?» | 🧾 kategoriyalar bo'yicha |
| «Bu hafta kim necha kun kelmadi?» | Davomat |
| «Noyabr maoshi qancha chiqadi?» | Maosh hisobi |
| «Kecha omborga qancha kirim, qancha chiqim bo'ldi?» | Kirim / chiqim summalari |

## 👤 Admin va 📦 Sklad nazoratchisi

Admin hammasini qila oladi (👥 foydalanuvchilar, 🎭 Rolni sinash ham). Sklad nazoratchisi — ombor, do'kon
xarajati, yo'qlama va katalog (davomat / maosh tahlilisiz).

### 📥 Kirim (Приход)

| Misol | Izoh |
|---|---|
| «Omborga 20 ta petlya 4500 so'mdan kirim qil, Alkan Groupdan» | Bitta karta |
| «3 karobka samorez keldi, karobkasi 90 ming, Rustam do'kon» | Karobka → dona avtomatik |
| «10 ta akfix kirim qil, Bozordan» (narx aytilmasa) | Oxirgi narx olinadi 🔁 |
| «5 ta skotch kirim, narxi keyin» | Narxsiz (sariq) — keyin 💰 narx |
| «20 ta petlya va 2 kg samorez kirim qil, Alkan» | 2 ta karta |

### 📤 Chiqim (Расход)

| Misol | Izoh |
|---|---|
| «5 ta samorez Olimjon ustaga chiqim qil» | Qoldiq tekshiriladi |
| «Shavkatga 2 ta akfix, sex uchun» | Izoh: «sex uchun» |
| «Omborga 20 ta petlya 4500 dan kirim qil va 5 ta samorez Olimjon ustaga chiqim qil» | Zanjir: 2 karta |

Yangi usta nomi birinchi marta yozilsa — Справочник ga avtomatik qo'shiladi.

### ✏️ Bugungi va o'tgan yozuvlarni tahrirlash / 🗑 o'chirish

| Misol | Nima bo'ladi |
|---|---|
| «Kechagi akfix kirimini o'chir» | Bot kechagi akfix kirimlarini topadi; bir nechta bo'lsa — ro'yxat chiqaradi, qaysi biri ekanini so'raydi |
| «Kecha kiritilgan akfix kirimini o'chirib, bugunga 15 ta yangidan kirim qil» | 2 karta: o'chirish + yangi kirim |
| «25-noyabrdagi petlya kirimi 20 emas, 25 ta edi» | Miqdor tahrirlanadi |
| «Bugungi skotch kirimining narxi 12 ming» | Narx yoziladi, narxsiz ro'yxatdan chiqadi |
| «Olimjonga bergan samorez 5 emas 3 ta edi» | Chiqim miqdori tahrirlanadi |
| «Kechagi Shavkat chiqimini o'chir» | Chiqim qatori o'chiriladi |
| «Kechagi benzin xarajatini 350 ga o'zgartir» | 🧾 summa 350 000 |
| «Bugungi suv xarajatini o'chir» | 🧾 qator o'chiriladi |
| «Kechagi do'kon xarajatini 520 qil» | 🏪 kun summasi |
| «24-noyabr do'kon xarajatini o'chir» | 🏪 kun tozalanadi (sariq — kiritilmagan) |

O'chirilgan qator Google Sheets jadvalidan butunlay olib tashlanadi (bo'sh qator qolmaydi); qoldiq, o'rtacha
narx va status formulalar bilan avtomatik qayta hisoblanadi. **Bossga yuborilgan do'kon kuni** o'zgartirilsa yoki
o'chirilsa — bot **sabab** so'raydi va Boss / Adminga ✏️ tuzatish xabari yuboriladi. Har bir o'zgarish audit
jurnaliga yoziladi; Jonli Dashboard va hisobotlar darhol yangilanadi.

Menyu orqali: **📦 Mahsulotlar → 🗂 Yozuvlarni tahrirlash** → Kirim / Chiqim / Boshqa xarajat / Do'kon →
yozuvni tanlang (yoki mahsulot nomi / sanani yozing) → ✏️ Miqdor / Narx / Summa yoki 🗑 O'chirish → ✅.

### 📦 Katalog, narx, ustalar, birliklar

| Misol | Izoh |
|---|---|
| «Yangi mahsulot: Petlya 35mm, dona, minimal 50 ta» | Kategoriya avtomatik, Мин. остаток > 0 majburiy |
| «Kromka Noch koma 1/19 qo'sh, minimal 2 rulon» | 🎞 Кромка ПВХ, рулон |
| «Petlya 35mm nomini Петля накладная 35мм ga o'zgartir» | Barcha varaqlarda |
| «Eski ruchka 224 mahsulotini o'chir» | Остатки va Справочник dan |
| «Akfix qoldig'iga 5 ta qo'sh» / «Akfixdan 3 tani ayir» | ⚖️ Qoldiqni to'g'rilash |
| «Skotch kirimiga narx qo'y: 12000» | 💰 Narxi yo'q tovar |
| «Ustalar ro'yxatiga Bekzodni qo'sh» / «Bekzodni ustalardan o'chir» | 👷 Ustalar |
| «O'lchov birligi «litr» qo'sh» | 📏 Birliklar |

### 🏪 Do'kon va 🧾 Boshqa xarajatlar

| Misol | Izoh |
|---|---|
| «Bugungi do'kon xarajati 450» | 450 000 so'm |
| «Kechagi do'kon xarajati 380 ming edi» | O'tgan kun |
| «Damas benzin 120 ming» | 🧾 Топливо / Бензин |
| «Stanok ta'miri 300, kecha» | 🧾 Ремонт |

## 👷 Usta

Usta faqat **qoldiqlarni ko'radi** (narxsiz) va **📤 Chiqim** qiladi. Kirim, narxlar, katalog, xarajatlar,
tahrirlash / o'chirish va Boss hisobotlari — yopiq.

| Misol | Natija |
|---|---|
| «Samorez qancha qoldi?» | Qoldiq (narxsiz) |
| «Qaysi tovarlar tugayapti?» | Tanqis tovarlar |
| «5 ta samorez menga chiqim qil» / «Olimjon ustaga 2 ta akfix» | Chiqim kartasi → ✅ |
| «Tilni rus tiliga o'zgartir» | Til almashadi |

Menyu: **📤 Chiqim** va **📊 Qoldiqlar**.

## 🧑‍🔧 Ishchi

Ishchi uchun AI yordamchi o'chiq (API sarflanmaydi). Menyu: **📅 Mening davomatim**, **📊 Qoldiqlar**.
`/help` — qisqa yo'riqnoma, `/lang` — til. Matn bilan til almashtirish ham ishlaydi: «Tilni rus tiliga o'zgartir».

## 🎭 Admin: rolni sinash

**🎭 Rolni sinash** → Boss / Sklad nazoratchisi / 👷 Usta / Ishchi — menyu va AI ruxsatlari shu rolga moslashadi
(ishchi rolida AI o'chadi). Qaytish: **🔙 Adminga qaytish** yoki `/admin`.

## Sozlamalar (.env)

| O'zgaruvchi | Standart | Izoh |
|---|---|---|
| `GEMINI_API_KEY` | — | https://aistudio.google.com/apikey (kalit bo'lmasa AI o'chiq) |
| `GEMINI_PRIMARY_MODEL` | `gemini-3.5-flash` | `GEMINI_MODEL` berilgan bo'lsa — o'sha |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` | Asosiy model 429 / 503 / 404 bersa — darhol shu model |
