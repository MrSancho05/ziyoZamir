"""🧾 Audit jurnali (SQLite `audit_log`): kim, qachon, qaysi yozuvni yaratdi / tahrirladi / o'chirdi."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from app.storage.database import Database


class AuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, user_id: Optional[int], user_name: str, action: str, target: str,
                  details: Optional[Dict[str, Any]] = None, source: str = "bot") -> None:
        await self._db.execute(
            "INSERT INTO audit_log (created_at, user_id, user_name, source, action, target, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), user_id, user_name, source, action, target,
             json.dumps(details or {}, ensure_ascii=False, default=str)))

    async def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = await self._db.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
