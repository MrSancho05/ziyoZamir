"""Yuborilgan hisobotlarning bo'limlari (qisqacha / batafsil / bo'limlar) — har bir til uchun.

Boss "📄 Batafsil" yoki "🏪 Do'kon batafsil" ni bosganda xabar shu matnlar bilan JOYIDA tahrirlanadi
(yangi xabar yuborilmaydi). Bot qayta ishga tushsa ham tugmalar ishlayveradi."""
from __future__ import annotations

import json
from typing import Dict, Optional

from app.storage.database import Database


class ReportCache:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, kind: str, key: str, lang: str, sections: Dict[str, str]) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO report_cache (kind, report_key, lang, payload) VALUES (?, ?, ?, ?)",
            (kind, key, lang, json.dumps(sections, ensure_ascii=False)))

    async def get(self, kind: str, key: str, lang: str) -> Optional[Dict[str, str]]:
        row = await self._db.fetchone(
            "SELECT payload FROM report_cache WHERE kind = ? AND report_key = ? AND lang = ?", (kind, key, lang))
        if row is None:                    # foydalanuvchi tilini o'zgartirgan bo'lsa: boshqa tildagi nusxa
            row = await self._db.fetchone(
                "SELECT payload FROM report_cache WHERE kind = ? AND report_key = ?", (kind, key))
        return json.loads(row["payload"]) if row else None
