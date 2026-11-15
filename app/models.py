from dataclasses import dataclass
from datetime import date

from app.core.permissions import Role

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class DeficitItem:
    name: str
    stock: float
    norm: float
    is_deficit: bool = True      # "Barcha mahsulotlar" ro'yxatida tanqis emaslari False


@dataclass(frozen=True, slots=True)
class User:
    id: int
    full_name: str
    username: str | None
    role: Role | None
    lang: str
    status: str
    worker_name: str | None = None   # ishchining yo'qlama jadvalidagi ismi (admin bog'laydi)

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE and self.role is not None


@dataclass(slots=True)
class AttendanceDraft:
    """Ochiq turgan yo'qlama oynasining holati. Har bir belgi bosilishi bilan jadvalga va bazaga
    darhol yoziladi, shuning uchun bu yerda faqat oynani chizish uchun nusxa saqlanadi."""
    day: date
    names: list[str]
    marks: list[str | None]    # "+" keldi, "-" sababsiz kelmadi, "S" sababli kelmadi, None = belgilanmagan
    existing: bool = False     # jadvalda oldindan belgilar bormidi
    from_calendar: bool = False   # kalendardan ochilgan ("Bekor qilish" kalendarga qaytaradi)
