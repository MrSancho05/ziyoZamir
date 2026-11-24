"""📊 Jonli Dashboard (Telegram Mini App) manzili va tugmalari — bitta joyda.

main.py ishga tushganda `set_webapp_url(settings.webapp_url)` chaqiradi. Manzil bo'sh yoki https:// emas bo'lsa,
hech qayerda dashboard tugmasi chiqmaydi (Telegram Mini App faqat HTTPS bilan ishlaydi, aks holda butun
klaviatura rad etiladi)."""
from __future__ import annotations

from typing import Dict, List
from urllib.parse import urlencode, urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, WebAppInfo

_WEBAPP_URL = ""


def set_webapp_url(url: str) -> bool:
    """True — manzil qabul qilindi va tugmalar yoqildi."""
    global _WEBAPP_URL
    url = (url or "").strip()
    _WEBAPP_URL = url if url.lower().startswith("https://") else ""
    return bool(_WEBAPP_URL)


def webapp_enabled() -> bool:
    return bool(_WEBAPP_URL)


def dashboard_url(lang: str) -> str:
    """Foydalanuvchi tili `?lang=` bilan qo'shiladi (Mini App shu tilda ochiladi)."""
    parts = urlsplit(_WEBAPP_URL)
    query = "&".join(q for q in (parts.query, urlencode({"lang": lang})) if q)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def dashboard_rows(t: Dict[str, str]) -> List[List[InlineKeyboardButton]]:
    """Hisobot klaviaturasining oxiriga qo'shiladigan qator: [📊 Jonli Dashboard]. Manzil yo'q bo'lsa — []."""
    if not _WEBAPP_URL:
        return []
    return [[InlineKeyboardButton(text=t["btn_live_dashboard"],
                                  web_app=WebAppInfo(url=dashboard_url(t.get("lang_code", "uz"))))]]
