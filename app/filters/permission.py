from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.core.permissions import Permission, has_permission
from app.models import User


class HasPermission(BaseFilter):
    """Foydalanuvchi roli kerakli ruxsatga ega bo'lsagina handlerni ishga tushiradi."""

    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and has_permission(user.role, self.permission)
