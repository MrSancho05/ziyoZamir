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


@dataclass(frozen=True, slots=True)
class User:
    id: int
    full_name: str
    username: str | None
    role: Role | None
    lang: str
    status: str

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE and self.role is not None


@dataclass(slots=True)
class AttendanceDraft:
    """Yo'qlama olinayotgan paytdagi vaqtinchalik holat (saqlanguncha xotirada turadi)."""
    day: date
    names: list[str]
    marks: list[str | None]    # "+" keldi, "-" sababsiz kelmadi, "S" sababli kelmadi, None = belgilanmagan
    existing: bool = False     # jadvalda oldindan belgilar bormidi
    saved: bool = False
    sent: bool = False
