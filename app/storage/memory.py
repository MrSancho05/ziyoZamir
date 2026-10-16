from app.models import AttendanceDraft, DeficitItem


class UserStore:
    """Vaqtinchalik keshlar: oxirgi tanqis ro'yxat va yo'qlama qoralamasi.

    Til va rollar bazada (storage/users.py) saqlanadi.
    """

    def __init__(self) -> None:
        self._deficit: dict[int, list[DeficitItem]] = {}
        self._drafts: dict[int, AttendanceDraft] = {}

    def get_deficit(self, user_id: int) -> list[DeficitItem]:
        return self._deficit.get(user_id, [])

    def set_deficit(self, user_id: int, items: list[DeficitItem]) -> None:
        self._deficit[user_id] = items

    def get_draft(self, user_id: int) -> AttendanceDraft | None:
        return self._drafts.get(user_id)

    def set_draft(self, user_id: int, draft: AttendanceDraft) -> None:
        self._drafts[user_id] = draft
