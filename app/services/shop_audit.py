"""Bossga yuborilgan do'kon xarajati keyin o'zgartirilsa: qisqa "✏️ Tuzatish (Audit)" xabari."""
from __future__ import annotations

from datetime import date
from html import escape
from typing import Dict

from app.services.payroll import fmt_money


def format_audit_notice(t: Dict[str, str], day: date, old: int, new: int, who: str, reason: str) -> str:
    diff = new - old
    sign = "+" if diff > 0 else "−"
    return t["shop_audit_notice"].format(date=day.strftime("%d.%m.%Y"), old=fmt_money(old), new=fmt_money(new),
                                         diff=f"{sign}{fmt_money(abs(diff))}", who=escape(who, quote=False),
                                         reason=escape(reason, quote=False))
