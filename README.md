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
