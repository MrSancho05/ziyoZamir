"""Xabar qabul qiluvchilar va ishonchli yetkazish.

- `staff_recipients`: mas'ul xodimlar = Admin + Sklad nazoratchisi (eslatma va ogohlantirishlar).
- `boss_circle`: Boss va unga yuboriladigan hamma narsaning NUSXASI boradigan Admin(lar).
- `deliver_once`: har bir qabul qiluvchiga alohida hisobda, kuniga/davrga BIR marta yetkazadi.
  Bitta qabul qiluvchidagi xato boshqalarga xalaqit bermaydi, yetib bormaganlariga keyinroq qayta uriniladi.
"""
import logging
from typing import Any, Callable

from aiogram import Bot

from app.core.permissions import Role
from app.locales.texts import get_texts
from app.models import User
from app.services.access import SEND_BLOCKED, SEND_OK, send_status
from app.storage.report_log import ReportLog
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)


def _unique(users: list[User]) -> list[User]:
    seen: set[int] = set()
    out: list[User] = []
    for u in users:
        if u.id not in seen:
            seen.add(u.id)
            out.append(u)
    return out


async def staff_recipients(repo: UserRepository) -> list[User]:
    """Eslatma/ogohlantirish oluvchilar: barcha Admin va barcha Sklad nazoratchilari (takrorlarsiz)."""
    return _unique(await repo.list_by_role(Role.ADMIN) + await repo.list_by_role(Role.STOREKEEPER))


async def boss_circle(repo: UserRepository) -> tuple[list[User], list[User]]:
    """(bosslar, admin nusxa oluvchilar). Boss ro'yxatida allaqachon bor admin qayta qo'shilmaydi."""
    bosses = _unique(await repo.list_by_role(Role.BOSS))
    boss_ids = {b.id for b in bosses}
    mirrors = [a for a in _unique(await repo.list_by_role(Role.ADMIN)) if a.id not in boss_ids]
    return bosses, mirrors


async def deliver_once(bot: Bot, log: ReportLog, users: list[User], kind: str, key: str,
                       build: Callable[[dict[str, str], str], str],
                       markup: Callable[[dict[str, str]], Any] | None = None) -> bool:
    """Har bir `users` a'zosiga (kind, key) uchun BIR martadan yuboradi. Yetkazilgani (yoki bot bloklangani)
    bazada shu foydalanuvchi uchun belgilanadi, shuning uchun qayta chaqirilganda faqat yetib bormaganlarga
    uriniladi. Hammasi hal bo'lsa True, vaqtinchalik xato bo'lsa False (chaqiruvchi keyinroq qayta uringan)."""
    all_done = True
    for user in users:
        user_kind = f"{kind}@{user.id}"
        if await log.was_sent(user_kind, key):
            continue
        t = get_texts(user.lang)
        extra = {"reply_markup": markup(t)} if markup else {}
        status = await send_status(bot, user.id, build(t, user.lang), **extra)
        if status in (SEND_OK, SEND_BLOCKED):
            await log.mark_sent(user_kind, key)
        else:
            all_done = False
    if not users:
        logger.warning("%s: qabul qiluvchi topilmadi (%s)", kind, key)
    return all_done
