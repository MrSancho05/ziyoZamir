import os
from dataclasses import dataclass
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
    page_size: int = 10


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return frozenset(ids)


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
        # Oylik yo'qlama varag'i nomi. Masalan: "Davomat 2026-10"
        attendance_tab_format=os.getenv("ATTENDANCE_TAB_FORMAT", "Davomat {year}-{month:02d}"),
        report_hour=int(os.getenv("REPORT_HOUR", "9")),
        reminder_hour=int(os.getenv("REMINDER_HOUR", "10")),
    )
