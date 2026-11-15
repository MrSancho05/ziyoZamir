import asyncio
import time

from app.models import AttendanceDraft, DeficitItem


class UserStore:
    """Vaqtinchalik keshlar: oxirgi tanqis ro'yxat, yo'qlama qoralamasi va "summa kutilmoqda" holati.

    Til va rollar bazada (storage/users.py) saqlanadi.
    """

    AWAIT_TTL = 600   # soniya: shundan keyin kutilayotgan kiritish bekor bo'ladi

    def __init__(self) -> None:
        # foydalanuvchi -> {"def": tanqis ro'yxat, "all": barcha mahsulotlar}
        self._lists: dict[int, dict[str, list[DeficitItem]]] = {}
        self._drafts: dict[int, AttendanceDraft] = {}
        self._draft_locks: dict[int, asyncio.Lock] = {}
        self._awaiting: dict[int, tuple[str, int, float]] = {}

    def get_products(self, user_id: int, kind: str) -> list[DeficitItem]:
        return self._lists.get(user_id, {}).get(kind, [])

    def set_products(self, user_id: int, kind: str, items: list[DeficitItem]) -> None:
        self._lists.setdefault(user_id, {})[kind] = items

    def get_deficit(self, user_id: int) -> list[DeficitItem]:
        return self.get_products(user_id, "def")

    def set_deficit(self, user_id: int, items: list[DeficitItem]) -> None:
        self.set_products(user_id, "def", items)

    def get_draft(self, user_id: int) -> AttendanceDraft | None:
        return self._drafts.get(user_id)

    def set_draft(self, user_id: int, draft: AttendanceDraft) -> None:
        self._drafts[user_id] = draft

    def clear_draft(self, user_id: int) -> AttendanceDraft | None:
        return self._drafts.pop(user_id, None)

    def draft_lock(self, user_id: int) -> asyncio.Lock:
        """Bir foydalanuvchining ketma-ket tez bosishlari bir-birini buzmasligi uchun."""
        return self._draft_locks.setdefault(user_id, asyncio.Lock())

    # --- foydalanuvchidan matn (summa) kutilmoqda ---
    def set_awaiting(self, user_id: int, kind: str, ymd: int) -> None:
        self._awaiting[user_id] = (kind, ymd, time.time())

    def get_awaiting(self, user_id: int) -> tuple[str, int] | None:
        item = self._awaiting.get(user_id)
        if item is None:
            return None
        kind, ymd, ts = item
        if time.time() - ts > self.AWAIT_TTL:
            del self._awaiting[user_id]
            return None
        return kind, ymd

    def clear_awaiting(self, user_id: int) -> None:
        self._awaiting.pop(user_id, None)
