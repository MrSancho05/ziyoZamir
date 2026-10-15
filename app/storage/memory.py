from app.models import DeficitItem


class UserStore:
    """Foydalanuvchining oxirgi tanqis ro'yxati (vaqtinchalik kesh).

    Til va rollar endi bazada (storage/users.py) saqlanadi.
    """

    def __init__(self) -> None:
        self._deficit: dict[int, list[DeficitItem]] = {}

    def get_deficit(self, user_id: int) -> list[DeficitItem]:
        return self._deficit.get(user_id, [])

    def set_deficit(self, user_id: int, items: list[DeficitItem]) -> None:
        self._deficit[user_id] = items
