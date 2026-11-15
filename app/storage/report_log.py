from app.storage.database import Database


class ReportLog:
    """Qaysi avtomatik hisobot yuborilganini eslab qoladi (ikki marta yuborilmasligi uchun)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def was_sent(self, kind: str, key: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM report_log WHERE kind = ? AND period_key = ?", (kind, key)
        )
        return row is not None

    async def has_any(self, kind: str) -> bool:
        row = await self._db.fetchone("SELECT 1 FROM report_log WHERE kind = ? LIMIT 1", (kind,))
        return row is not None

    async def mark_sent(self, kind: str, key: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO report_log (kind, period_key) VALUES (?, ?)", (kind, key)
        )
