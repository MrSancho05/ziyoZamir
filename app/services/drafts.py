"""✍️ Tasdiqlash kutayotgan yozuvlar (qoralamalar): AI yordamchi va 🗂 menyu bir xil karta / tugmalardan foydalanadi.

Har bir qoralama: noyob `id`, yaratgan `user_id`, tur, ma'lumot (payload) va 10 daqiqalik amal muddati.
«✅ Tasdiqlash» faqat bir marta bajariladi (`take`). Xotirada saqlanadi: bot qayta ishga tushsa — eskiradi."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

DRAFT_TTL = 600.0


@dataclass
class Draft:
    id: str
    user_id: int
    kind: str
    payload: Dict[str, Any]
    card: str
    source: str = "ai"                  # ai | menu
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > DRAFT_TTL


class DraftStore:
    def __init__(self) -> None:
        self._items: Dict[str, Draft] = {}

    def _cleanup(self) -> None:
        for k in [k for k, d in self._items.items() if d.expired]:
            self._items.pop(k, None)

    def add(self, user_id: int, kind: str, payload: Dict[str, Any], card: str, source: str = "ai") -> Draft:
        self._cleanup()
        draft = Draft(secrets.token_hex(5), user_id, kind, payload, card, source)
        self._items[draft.id] = draft
        return draft

    def get(self, draft_id: str) -> Optional[Draft]:
        self._cleanup()
        return self._items.get(draft_id)

    def take(self, draft_id: str) -> Optional[Draft]:
        """Bir marta: ikkinchi bosishda None (ikki marta yozilmaydi)."""
        self._cleanup()
        return self._items.pop(draft_id, None)


DRAFTS = DraftStore()
