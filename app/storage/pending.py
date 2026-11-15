from dataclasses import dataclass

from app.storage.database import Database


@dataclass(frozen=True, slots=True)
class PendingReport:
    kind: str
    key: str
    user_id: int | None
    ready_at: int


class PendingReports:
    """Tayyor bo'lgan, lekin hali bossga yuborilmagan hisobotlar.
    Xodim "Bossga yuborish" ni bosmasa, N daqiqadan keyin avtomatik yuboriladi."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def ready(self, kind: str, key: str, user_id: int | None, ts: int) -> None:
        """Hisobot tayyor (saqlandi yoki tahrirlandi): taymer qaytadan boshlanadi."""
        await self._db.execute(
            "INSERT INTO pending_reports (kind, report_key, user_id, ready_at, sent_at, auto) "
            "VALUES (?, ?, ?, ?, NULL, 0) "
            "ON CONFLICT(kind, report_key) DO UPDATE SET user_id = excluded.user_id, "
            "ready_at = excluded.ready_at, sent_at = NULL, auto = 0",
            (kind, key, user_id, ts),
        )

    async def mark_sent(self, kind: str, key: str, ts: int, auto: bool = False) -> None:
        await self._db.execute(
            "UPDATE pending_reports SET sent_at = ?, auto = ? WHERE kind = ? AND report_key = ?",
            (ts, int(auto), kind, key),
        )

    async def due(self, cutoff_ts: int, kinds: tuple[str, ...] | None = None) -> list[PendingReport]:
        """Yuborish vaqti kelgan hisobotlar. `kinds` berilsa, faqat shu turlar qaytariladi."""
        rows = await self._db.fetchall(
            "SELECT * FROM pending_reports WHERE sent_at IS NULL AND ready_at <= ? ORDER BY ready_at",
            (cutoff_ts,),
        )
        if kinds is not None:
            rows = [r for r in rows if r["kind"] in kinds]
        return [PendingReport(r["kind"], r["report_key"], r["user_id"], r["ready_at"]) for r in rows]

    async def is_sent(self, kind: str, key: str) -> bool | None:
        """None: bunday yozuv yo'q; True/False: yuborilgan/yuborilmagan."""
        row = await self._db.fetchone(
            "SELECT sent_at FROM pending_reports WHERE kind = ? AND report_key = ?", (kind, key))
        return None if row is None else row["sent_at"] is not None
