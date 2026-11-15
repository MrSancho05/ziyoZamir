# Botni 24/7 ishlatish (Linux VPS, Ubuntu)

Bot shunchaki Python dasturi: u ishlab turgan kompyuter o'chsa, bot ham to'xtaydi.
PyCharm shart emas, lekin dastur doim yoqilgan serverda ishlashi kerak.

## 1. Bir martalik o'rnatish (serverda)
    sudo apt update && sudo apt install -y python3-venv
    sudo useradd -r -m botuser
    sudo mkdir -p /opt/ziyozamir_bot && sudo chown botuser: /opt/ziyozamir_bot

Loyiha fayllarini (`.env`, `credentials/` bilan birga) `/opt/ziyozamir_bot` ga ko'chiring
(WinSCP, FileZilla yoki `scp -r`). Keyin:

    sudo -u botuser bash -c "cd /opt/ziyozamir_bot && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    sudo cp /opt/ziyozamir_bot/deploy/ziyozamir-bot.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now ziyozamir-bot

`enable` server qayta yoqilganda botni avtomatik ishga tushiradi,
`Restart=always` bot xatodan yiqilsa o'zi qayta yoqadi.

## 2. Kundalik buyruqlar
    sudo systemctl status ziyozamir-bot      # holati
    sudo journalctl -u ziyozamir-bot -f      # jonli loglar
    sudo systemctl restart ziyozamir-bot     # o'zgarish kiritgandan keyin

## 3. Kodni yangilash
Kompyuteringizda o'zgartirib, yangi fayllarni serverga ko'chiring
(`.env`, `credentials/`, `data/` ga tegmang), so'ng `restart` qiling.

## Muhim
- Bitta token bilan bot FAQAT BITTA joyda ishlashi kerak. Server ishlab turganda
  PyCharm'da shu tokenni ishga tushirsangiz "Conflict" xatosi chiqadi.
  Sinash uchun @BotFather'da alohida test bot yarating va uning tokenini
  kompyuteringizdagi `.env` ga yozing.
- `data/bot.db` (foydalanuvchilar va rollar) ni vaqti-vaqti bilan nusxalab turing.
