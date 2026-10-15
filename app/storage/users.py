import sqlite3

from app.core.permissions import Role
from app.models import STATUS_ACTIVE, STATUS_PENDING, User
from app.storage.database import Database

_ORDER = (
    "ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'boss' THEN 1 "
    "WHEN 'storekeeper' THEN 2 ELSE 3 END, full_name"
)


def _to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        full_name=row["full_name"],
        username=row["username"],
        role=Role(row["role"]) if row["role"] else None,
        lang=row["lang"],
        status=row["status"],
    )


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, user_id: int) -> User | None:
        row = await self._db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        return _to_user(row) if row else None

    async def create_pending(self, user_id: int, full_name: str,
                             username: str | None, lang: str) -> bool:
        """Yangi so'rov yaratadi. Foydalanuvchi allaqachon bor bo'lsa False qaytaradi."""
        if await self.get(user_id):
            return False
        await self._db.execute(
            "INSERT OR IGNORE INTO users (id, full_name, username, role, lang, status) "
            "VALUES (?, ?, ?, NULL, ?, ?)",
            (user_id, full_name, username, lang, STATUS_PENDING),
        )
        return True

    async def activate(self, user_id: int, role: Role) -> None:
        await self._db.execute(
            "UPDATE users SET role = ?, status = ? WHERE id = ?",
            (role.value, STATUS_ACTIVE, user_id),
        )

    async def set_role(self, user_id: int, role: Role) -> None:
        await self._db.execute("UPDATE users SET role = ? WHERE id = ?", (role.value, user_id))

    async def set_lang(self, user_id: int, lang: str) -> None:
        await self._db.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))

    async def update_profile(self, user_id: int, full_name: str, username: str | None) -> None:
        await self._db.execute(
            "UPDATE users SET full_name = ?, username = ? WHERE id = ?",
            (full_name, username, user_id),
        )

    async def delete(self, user_id: int) -> None:
        await self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    async def list_pending(self) -> list[User]:
        rows = await self._db.fetchall(
            "SELECT * FROM users WHERE status = ? ORDER BY created_at", (STATUS_PENDING,)
        )
        return [_to_user(r) for r in rows]

    async def list_active(self) -> list[User]:
        rows = await self._db.fetchall(
            f"SELECT * FROM users WHERE status = ? {_ORDER}", (STATUS_ACTIVE,)
        )
        return [_to_user(r) for r in rows]

    async def list_by_role(self, role: Role) -> list[User]:
        rows = await self._db.fetchall(
            "SELECT * FROM users WHERE role = ? AND status = ?", (role.value, STATUS_ACTIVE)
        )
        return [_to_user(r) for r in rows]

    async def ensure_admins(self, admin_ids: frozenset[int]) -> None:
        """.env dagi ADMIN_IDS har ishga tushganda admin qilib tiklanadi."""
        for uid in admin_ids:
            await self._db.execute(
                "INSERT INTO users (id, full_name, username, role, lang, status) "
                "VALUES (?, 'Admin', NULL, 'admin', 'uz', 'active') "
                "ON CONFLICT(id) DO UPDATE SET role = 'admin', status = 'active'",
                (uid,),
            )
