import asyncio
from datetime import date

from app.services.attendance_grid import EXCUSED, MINUS, PLUS
from app.storage.database import Database

STATUS_PRESENT = "present"
STATUS_UNEXCUSED = "unexcused"
STATUS_EXCUSED = "excused"

_MARK_TO_STATUS = {PLUS: STATUS_PRESENT, MINUS: STATUS_UNEXCUSED, EXCUSED: STATUS_EXCUSED}


def mark_status(mark: str | None) -> str | None:
    """Jadvaldagi belgi (+ / - / S) -> bazadagi holat nomi."""
    return _MARK_TO_STATUS.get(mark) if mark else None


class AttendanceStore:
    """Yo'qlamaning bazadagi nusxasi:
    - attendance_marks: har bir ishchining kundagi holati (har belgi bosilishi bilan yangilanadi);
    - attendance_sent: shu kun hisoboti bossga yuborilganmi (qo'lda yoki avtomatik) - kuniga BIR marta;
    - attendance_notices: ishchiga yuborilgan xabar (bir xil holat ikki marta yuborilmasligi uchun).
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # Bossga yuborishni tekshirish + yuborish + belgilash bitta qadam bo'lsin (ikki marta yuborilmasin)
        self.send_lock = asyncio.Lock()

    # ---------------- belgilar ----------------
    async def record_marks(self, day: date, rows: list[tuple[str, str | None]],
                           by: int | None, ts: int) -> None:
        """(ishchi ismi, belgi) juftliklarini yozadi. Belgi None bo'lsa, yozuv o'chiriladi."""
        for name, mark in rows:
            status = mark_status(mark)
            if status is None:
                await self._db.execute(
                    "DELETE FROM attendance_marks WHERE day = ? AND worker_name = ?",
                    (day.isoformat(), name))
                continue
            await self._db.execute(
                "INSERT INTO attendance_marks (day, worker_name, status, marked_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(day, worker_name) DO UPDATE SET status = excluded.status, "
                "marked_by = excluded.marked_by, updated_at = excluded.updated_at",
                (day.isoformat(), name, status, by, ts))

    # ---------------- bossga yuborilganmi ----------------
    async def is_sent(self, day: date) -> bool:
        row = await self._db.fetchone("SELECT 1 FROM attendance_sent WHERE day = ?", (day.isoformat(),))
        if row is not None:
            return True
        # Eski versiyada yuborilgan hisobotlar (pending_reports) ham hisobga olinadi
        legacy = await self._db.fetchone(
            "SELECT 1 FROM pending_reports WHERE kind = 'attendance' AND report_key = ? "
            "AND sent_at IS NOT NULL", (day.isoformat(),))
        return legacy is not None

    async def mark_sent(self, day: date, ts: int, auto: bool = False, by: int | None = None) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO attendance_sent (day, sent_at, auto, sent_by) VALUES (?, ?, ?, ?)",
            (day.isoformat(), ts, int(auto), by))

    # ---------------- ishchiga xabarlar ----------------
    async def get_notice(self, day: date, worker_name: str) -> str | None:
        row = await self._db.fetchone(
            "SELECT status FROM attendance_notices WHERE day = ? AND worker_name = ?",
            (day.isoformat(), worker_name))
        return row["status"] if row else None

    async def set_notice(self, day: date, worker_name: str, status: str, ts: int) -> None:
        await self._db.execute(
            "INSERT INTO attendance_notices (day, worker_name, status, sent_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(day, worker_name) DO UPDATE SET status = excluded.status, "
            "sent_at = excluded.sent_at",
            (day.isoformat(), worker_name, status, ts))
