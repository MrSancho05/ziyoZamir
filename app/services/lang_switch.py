"""🌐 Matndan tilni almashtirish so'rovini aniqlash (API chaqirilmaydi): «Tilni rus tiliga o'zgartir»,
«O'zbek tiliga o'tkaz», «Смени язык на русский», «Переключи на узбекский». Ovozli so'rov — AI orqali
(`change_language` vositasi)."""
from __future__ import annotations

import re
from typing import Optional

_LANG_WORD = re.compile(r"(\btil|язык|language|\blang\b)", re.IGNORECASE)
_EXPLICIT = re.compile(r"(ruschaga|ruschada|на\s+русск|по-русск|o['‘’`ʻ]?zbekchaga|ozbekchaga|на\s+узбекск|по-узбекск)",
                       re.IGNORECASE)
_RU = re.compile(r"(\brus\b|\brus\s*til|ruscha|русск|russian)", re.IGNORECASE)
_UZ = re.compile(r"(o['‘’`ʻ]?zbek|ozbek|uzbek|узбек|ўзбек)", re.IGNORECASE)


def detect(text: str) -> Optional[str]:
    """'ru' / 'uz' — aniq so'rov bo'lsa; aks holda None. Qisqa buyruq bo'lishi kerak (≤ 8 so'z)."""
    s = str(text or "").strip()
    if not s or len(s.split()) > 8:
        return None
    if not (_LANG_WORD.search(s) or _EXPLICIT.search(s)):
        return None
    ru, uz = bool(_RU.search(s)), bool(_UZ.search(s))
    if ru == uz:                      # ikkalasi ham / hech biri — noaniq
        return None
    return "ru" if ru else "uz"
