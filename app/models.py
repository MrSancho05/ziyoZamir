from dataclasses import dataclass

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
