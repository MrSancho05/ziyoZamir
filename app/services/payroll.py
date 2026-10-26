"""Maosh hisobi. Google Sheets ga bog'liq emas.

Qoida: kunlik ulush = oylik maosh / oydagi ish kunlari (dushanba-juma).
Ushlanma = kunlik ulush * (kelmagan kunlar - ishlangan shanbalar). Dushanba-juma belgilari sanaladi.

Shanba ixtiyoriy: kelmasa ushlanma yo'q. Ishlagan har bir shanba shu oydagi kelmagan
kunlardan 1 tasini kamaytiradi (nolgacha; qo'shimcha haq to'lanmaydi).
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.attendance_grid import (
    EXCUSED,
    MINUS,
    PLUS,
    GridRow,
    PeriodStats,
    is_saturday,
    is_workday,
)


def working_days_in_month(year: int, month: int) -> int:
    days = calendar.monthrange(year, month)[1]
    return sum(1 for d in range(1, days + 1) if is_workday(date(year, month, d)))


def parse_money(value: Any) -> int | None:
    """'2,000,000uzs', '2 000 000', '2000000' -> 2000000. Bo'sh/noto'g'ri -> None."""
    s = re.sub(r"[^\d.,]", "", str(value))
    if not re.search(r"\d", s):
        return None
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):        # ming ajratgichlar
        s = re.sub(r"[.,]", "", s)
    elif re.fullmatch(r"\d+[.,]\d{1,2}", s):             # kasr
        s = s.replace(",", ".")
    else:
        s = re.sub(r"[.,]", "", s)
    return int(float(s) + 0.5)


def fmt_money(n: int) -> str:
    return f"{n:,}".replace(",", "\u00a0")


def _norm(v: Any) -> str:
    return (str(v).strip().lower()
            .replace("’", "'").replace("ʻ", "'").replace("ʼ", "'").replace("`", "'"))


def find_money_cols(values: list[list[Any]], day_cols: dict[int, int],
                    rows: tuple[int, int] = (0, 1)) -> tuple[int | None, int | None]:
    """Sarlavha bo'yicha (1- yoki 2-qatorda) maosh ustunlarini topadi.
    `rows`: sarlavha qatorlari (0 dan): blokning oy nomi qatori va kun raqamlari qatori.
    'Oylik maosh...' -> (oylik ustuni), '...berilishi kerak...' -> (to'lanadigan ustuni)."""
    last_day_col = max(day_cols.values()) if day_cols else -1
    salary = payable = None
    for r in rows:
        row = values[r] if r < len(values) else []
        for ci, v in enumerate(row):
            if ci <= last_day_col:
                continue
            s = _norm(v)
            if not s:
                continue
            if payable is None and ("berilishi" in s or "to'lanadigan" in s or "tolanadigan" in s):
                payable = ci
            elif salary is None and s.startswith("oylik"):
                salary = ci
    return salary, payable


@dataclass(frozen=True, slots=True)
class WorkerPay:
    name: str
    salary: int | None
    deduct_days: int
    deduction: int
    payable: int | None
    saturdays: int = 0      # shu oyda ishlagan shanbalar soni
    compensated: int = 0    # shanba hisobiga kamaytirilgan kelmagan kunlar


@dataclass(slots=True)
class MonthReport:
    stats: PeriodStats
    pays: list[WorkerPay] | None   # None: maosh ustunlari topilmadi
    work_days: int                 # oydagi jami ish kunlari
    deduct_excused: bool
    tab_found: bool = True


def compute_pay(rows: list[GridRow], salaries: dict[int, int | None], year: int, month: int,
                deduct_excused: bool) -> list[WorkerPay]:
    work_days = working_days_in_month(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    result: list[WorkerPay] = []
    for r in rows:
        absences = saturdays = 0
        for day, mark in r.marks.items():
            if day > days_in_month:
                continue
            d = date(year, month, day)
            if is_workday(d):
                if mark == MINUS or (mark == EXCUSED and deduct_excused):
                    absences += 1
            elif is_saturday(d) and mark == PLUS:
                saturdays += 1
        compensated = min(saturdays, absences)
        days = absences - compensated
        salary = salaries.get(r.row)
        if salary is None:
            result.append(WorkerPay(r.name, None, days, 0, None, saturdays, compensated))
            continue
        deduction = min(salary, int(salary * days / work_days + 0.5)) if work_days else 0
        result.append(WorkerPay(r.name, salary, days, deduction, salary - deduction,
                                saturdays, compensated))
    return result
