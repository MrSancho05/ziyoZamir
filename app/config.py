import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    credentials_file: Path
    spreadsheet_name: str
    worksheet_name: str
    fonts_dir: Path
    db_path: Path
    timezone: ZoneInfo
    attendance_tab_format: str
    report_hour: int
    reminder_hour: int
    deduct_excused: bool
    shop_tab_format: str
    inflow_tab: str
    outflow_tab: str
    directory_tab: str
    shop_reminder_at: time
    shop_deadline_at: time
    attendance_deadline_at: time
    attendance_send_at: time
    flows_report_at: time
    activity_check_at: time
    auto_send_minutes: int
    attendance_reminder_at: time
    flows_reminder_at: time
    digest_at: time
    summary_at: time
    unpriced_first_minutes: int
    unpriced_warn_at: time
    unpriced_deadline_at: time
    other_expenses_tab: str
    webapp_url: str = ""
    gemini_api_key: str = ""                        # 🤖 AI yordamchi (bo'sh bo'lsa o'chiq)
    gemini_model: str = "gemini-3.5-flash"              # asosiy model (GEMINI_PRIMARY_MODEL / GEMINI_MODEL)
    gemini_fallback_model: str = "gemini-3.5-flash-lite"   # asosiy 429 / 503 / 404 bersa
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080
    page_size: int = 10


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return frozenset(ids)


def _parse_hm(name: str, default: str) -> time:
    raw = os.getenv(name, default).strip()
    try:
        hours, minutes = raw.split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        raise RuntimeError(f".env dagi {name} SS:DD ko'rinishida bo'lishi kerak (masalan 11:30)") from None


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(".env faylida BOT_TOKEN topilmadi")

    try:
        admin_ids = _parse_ids(os.getenv("ADMIN_IDS", ""))
    except ValueError:
        raise RuntimeError(".env dagi ADMIN_IDS faqat raqamlardan iborat bo'lishi kerak") from None
    if not admin_ids:
        raise RuntimeError(".env faylida ADMIN_IDS topilmadi (administratorning Telegram ID si)")

    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        credentials_file=BASE_DIR / os.getenv(
            "GOOGLE_CREDENTIALS_FILE", "credentials/ziyozamirbot.json"
        ),
        spreadsheet_name=os.getenv("SPREADSHEET_NAME", "Система Мебельного Завода ЗЗМ"),
        worksheet_name=os.getenv("WORKSHEET_NAME", "Остатки и Расходы"),
        fonts_dir=BASE_DIR / "assets" / "fonts",
        db_path=BASE_DIR / os.getenv("DB_PATH", "data/bot.db"),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent")),
        # Yillik yo'qlama varag'i nomi. Masalan: "Davomat 2026". Oylar shu varaqda blok-blok joylashadi
        attendance_tab_format=os.getenv("ATTENDANCE_TAB_FORMAT", "Davomat {year}"),
        report_hour=int(os.getenv("REPORT_HOUR", "9")),
        reminder_hour=int(os.getenv("REMINDER_HOUR", "10")),
        # 0 (standart): sababli kelmagan kunlar maoshdan ushlanmaydi; 1: ushlanadi
        deduct_excused=os.getenv("DEDUCT_EXCUSED", "0").strip().lower() in ("1", "true", "yes", "ha"),
        # Do'kon xarajatlari varag'i (yillik): oylar blok bo'lib joylashadi. Masalan "Магазин 2026"
        shop_tab_format=os.getenv("SHOP_TAB_FORMAT", "Магазин {year}"),
        inflow_tab=os.getenv("INFLOW_TAB", "Приход"),
        outflow_tab=os.getenv("OUTFLOW_TAB", "Расход"),
        # Do'konlar (Поставщики) va oluvchilar (Ответственные) ro'yxati
        directory_tab=os.getenv("DIRECTORY_TAB", "Справочник"),
        shop_reminder_at=_parse_hm("SHOP_REMINDER_TIME", "10:00"),
        shop_deadline_at=_parse_hm("SHOP_DEADLINE_TIME", "11:30"),
        attendance_deadline_at=_parse_hm("ATTENDANCE_DEADLINE_TIME", "14:30"),
        # Yo'qlama shu vaqtgacha bossga yuborilmagan bo'lsa, bot o'zi BIR MARTA yuboradi
        attendance_send_at=_parse_hm("ATTENDANCE_AUTO_SEND_TIME", "14:30"),
        flows_report_at=_parse_hm("FLOWS_REPORT_TIME", "18:30"),
        activity_check_at=_parse_hm("ACTIVITY_CHECK_TIME", "19:00"),
        auto_send_minutes=int(os.getenv("AUTO_SEND_MINUTES", "30")),
        # 13:30 yo'qlama eslatmasi (muddat: ATTENDANCE_DEADLINE_TIME, 14:30)
        attendance_reminder_at=_parse_hm("ATTENDANCE_REMINDER_TIME", "13:30"),
        # 17:30 kirim/chiqim eslatmasi (muddat: FLOWS_REPORT_TIME, 18:30)
        flows_reminder_at=_parse_hm("FLOWS_REMINDER_TIME", "17:30"),
        # Shanba va oy oxiri: yig'ma hisobot vaqti
        digest_at=_parse_hm("DIGEST_TIME", "20:30"),
        # Dushanba–shanba: Boss va Adminga kunlik ijroiya xulosasi
        summary_at=_parse_hm("SUMMARY_TIME", "20:00"),
        # Narxsiz kirim: 1-eslatma (daqiqa), 11:00 qat'iy ogohlantirish, 12:00 — Boss nazoratiga
        unpriced_first_minutes=int(os.getenv("UNPRICED_FIRST_REMINDER_MINUTES", "60")),
        unpriced_warn_at=_parse_hm("UNPRICED_DEADLINE_WARN_TIME", "11:00"),
        unpriced_deadline_at=_parse_hm("UNPRICED_DEADLINE_TIME", "12:00"),
        # 🧾 Boshqa xarajatlar varag'i (bot o'zi yaratadi)
        other_expenses_tab=os.getenv("OTHER_EXPENSES_TAB", "Прочие расходы"),
        # 📊 Jonli Dashboard (Telegram Mini App). Tugma faqat HTTPS manzil berilganda chiqadi
        webapp_url=os.getenv("WEBAPP_URL", "").strip(),
        webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0").strip() or "0.0.0.0",
        webapp_port=int(os.getenv("WEBAPP_PORT", "8080")),
        # 🤖 AI yordamchi (Google Gemini, bepul tarif). Kalit bo'lmasa — yordamchi o'chiq, bot avvalgidek ishlaydi
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=(os.getenv("GEMINI_PRIMARY_MODEL", "").strip() or os.getenv("GEMINI_MODEL", "").strip()
                      or "gemini-3.5-flash"),
        gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite").strip()
        or "gemini-3.5-flash-lite",
    )
