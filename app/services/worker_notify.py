"""Ishchilarga kunlik davomat xabarlari: bugun keldingiz / kelmadingiz (sababli yoki sababsiz)."""
import logging
import time
from datetime import date

from aiogram import Bot

from app.core.permissions import Role
from app.locales.texts import get_texts
from app.services.access import safe_send
from app.services.attendance_grid import PLUS, is_saturday
from app.storage.attendance_store import (
    STATUS_EXCUSED,
    STATUS_PRESENT,
    STATUS_UNEXCUSED,
    AttendanceStore,
    mark_status,
)
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)

_TEXT_KEY = {
    STATUS_PRESENT: "worker_att_present",
    STATUS_UNEXCUSED: "worker_att_absent",
    STATUS_EXCUSED: "worker_att_excused",
}


def norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


async def notify_workers(bot: Bot, repo: UserRepository, store: AttendanceStore, day: date,
                         names: list[str], marks: list[str | None]) -> int:
    """Berilgan ishchilarga ularning shu kundagi holatini yuboradi (jadvaldagi ismi bilan bog'langan
    ishchi foydalanuvchilarga). Xuddi shu holat shu kun uchun allaqachon yuborilgan bo'lsa, qayta
    yubormaydi; holat o'zgargan bo'lsa, «yangilandi» belgisi bilan yuboradi.
    Shanba (ixtiyoriy kun): faqat kelganlarga xabar boradi. Yuborilgan xabarlar soni qaytariladi."""
    users_by_name: dict[str, list] = {}
    for user in await repo.list_by_role(Role.WORKER):
        if user.worker_name:
            users_by_name.setdefault(norm_name(user.worker_name), []).append(user)
    if not users_by_name:
        return 0

    saturday = is_saturday(day)
    sent = 0
    for name, mark in zip(names, marks):
        status = mark_status(mark)
        if status is None or (saturday and mark != PLUS):
            continue
        recipients = users_by_name.get(norm_name(name))
        if not recipients:
            continue
        previous = await store.get_notice(day, name)
        if previous == status:
            continue
        delivered = False
        for user in recipients:
            t = get_texts(user.lang)
            text = t[_TEXT_KEY[status]].format(date=day.strftime("%d.%m.%Y"))
            if previous is not None:
                text = t["worker_att_changed"] + text
            delivered = await safe_send(bot, user.id, text) or delivered
        if delivered:
            await store.set_notice(day, name, status, int(time.time()))
            sent += 1
    if sent:
        logger.info("Ishchilarga davomat xabari yuborildi (%s): %d kishi", day.isoformat(), sent)
    return sent
