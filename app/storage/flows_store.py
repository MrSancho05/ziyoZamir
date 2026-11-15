import json
from dataclasses import dataclass

from app.storage.database import Database


@dataclass(frozen=True, slots=True)
class Snapshot:
    day: str
    hash: str
    rows: list[tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class StoredRow:
    day: str
    label: str
    qty: float | None
    amount: int | None


class FlowStore:
    """Prixod/Rasxod varaqlarining kunlik "oxirgi holati" va kun davomida qo'shilgan qatorlar."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_snapshot(self, sheet: str) -> Snapshot | None:
        row = await self._db.fetchone("SELECT * FROM flow_snapshots WHERE sheet = ?", (sheet,))
        if row is None:
            return None
        return Snapshot(row["day"], row["hash"], [tuple(r) for r in json.loads(row["rows_json"])])

    async def save_snapshot(self, sheet: str, day: str, digest: str, rows: list[tuple[str, ...]]) -> None:
        await self._db.execute(
            "INSERT INTO flow_snapshots (sheet, day, hash, rows_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(sheet) DO UPDATE SET day = excluded.day, hash = excluded.hash, "
            "rows_json = excluded.rows_json",
            (sheet, day, digest, json.dumps(rows, ensure_ascii=False)),
        )

    async def add_rows(self, sheet: str, day: str, rows: list[tuple[str, float | None, int | None]]) -> None:
        for label, qty, amount in rows:
            await self._db.execute(
                "INSERT INTO flow_rows (day, sheet, label, qty, amount) VALUES (?, ?, ?, ?, ?)",
                (day, sheet, label, qty, amount),
            )

    async def replace_rows(self, sheet: str, day: str, rows: list[tuple[str, float | None, int | None]]) -> None:
        """Shu kunning yozuvlarini to'liq almashtiradi (takroriy chaqiruvda ikkilanib qolmaydi)."""
        await self._db.execute("DELETE FROM flow_rows WHERE sheet = ? AND day = ?", (sheet, day))
        await self.add_rows(sheet, day, rows)

    async def rows_between(self, sheet: str, start: str, end: str) -> list[StoredRow]:
        rows = await self._db.fetchall(
            "SELECT day, label, qty, amount FROM flow_rows WHERE sheet = ? AND day >= ? AND day <= ? "
            "ORDER BY day, id", (sheet, start, end))
        return [StoredRow(r["day"], r["label"], r["qty"], r["amount"]) for r in rows]
