"""Uch qatlamli keshni bir vaqtda tozalash: har qanday yozish / tahrirlash / o'chirishdan keyin.

Ro'yxatdan o'tganlar (main.py): ombor katalogi va Приход/Расход keshi (WarehouseService), «Прочие расходы»
keshi (OtherExpensesService), Jonli Dashboard hisob-kitob keshi (DashboardService). Shunda /dashboard,
botdagi qoldiq / xarajat so'rovlari, PDF/CSV va hisobotlar darhol yangi raqamlarni ko'rsatadi.
Hech narsani import qilmaydi (aylanma importlar bo'lmasligi uchun)."""
from __future__ import annotations

import logging
from typing import Callable, List

logger = logging.getLogger(__name__)

_listeners: List[Callable[[], None]] = []
_busy = False


def register(fn: Callable[[], None]) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def invalidate_all() -> None:
    global _busy
    if _busy:                      # tinglovchi ichidan qayta chaqirilsa — cheksiz aylanish bo'lmasin
        return
    _busy = True
    try:
        for fn in list(_listeners):
            try:
                fn()
            except Exception:
                logger.warning("Keshni tozalab bo'lmadi: %r", fn, exc_info=True)
    finally:
        _busy = False
