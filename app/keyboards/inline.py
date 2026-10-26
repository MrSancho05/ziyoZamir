from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.permissions import ROLE_ORDER, Role
from app.keyboards.callbacks import AccessCb, AttCb, DownloadCb, LangCb, MenuCb, PageCb, ViewCb
from app.models import AttendanceDraft, User
from app.services.attendance_grid import EXCUSED, MINUS, PLUS

ASSIGNABLE_ON_REQUEST = (Role.BOSS, Role.STOREKEEPER, Role.WORKER)


def _btn(text: str, cb) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def language_kb() -> InlineKeyboardMarkup:
    return _kb([[
        _btn("O'zbekcha 🇺🇿", LangCb(code="uz")),
        _btn("Русский 🇷🇺", LangCb(code="ru")),
    ]])


def deficit_menu_kb(t: dict[str, str], can_download: bool) -> InlineKeyboardMarkup:
    rows = [[_btn(t["btn_view"], PageCb(page=0))]]
    if can_download:
        rows.append([_btn(t["btn_download"], MenuCb(action="download"))])
    return _kb(rows)


def download_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([
        [
            _btn(t["pdf_btn"], DownloadCb(fmt="pdf")),
            _btn(t["csv_btn"], DownloadCb(fmt="csv")),
        ],
        [_btn(t["back"], MenuCb(action="back"))],
    ])


def pagination_kb(page: int, total_pages: int, t: dict[str, str]) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(_btn(t["btn_prev"], PageCb(page=page - 1)))
    nav.append(_btn(f"📄 {page + 1}/{total_pages}", MenuCb(action="noop")))
    if page < total_pages - 1:
        nav.append(_btn(t["btn_next"], PageCb(page=page + 1)))

    jumps = []
    if page > 0:
        jumps.append(_btn(t["btn_first"], PageCb(page=0)))
    if page < total_pages - 1:
        jumps.append(_btn(t["btn_last"], PageCb(page=total_pages - 1)))

    rows = [nav]
    if jumps:
        rows.append(jumps)
    rows.append([_btn(t["back_to_main"], MenuCb(action="back"))])
    return _kb(rows)


# ---------- Admin: foydalanuvchilarni boshqarish ----------
def request_kb(t: dict[str, str], uid: int, with_back: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_btn(t[f"role_{r.value}"], AccessCb(action="approve", uid=uid, role=r.value))]
        for r in ASSIGNABLE_ON_REQUEST
    ]
    rows.append([_btn(t["btn_reject"], AccessCb(action="reject", uid=uid))])
    if with_back:
        rows.append([_btn(t["back"], AccessCb(action="list"))])
    return _kb(rows)


def users_list_kb(t: dict[str, str], pending: list[User], active: list[User]) -> InlineKeyboardMarkup:
    rows = [[_btn(f"🆕 {u.full_name}", AccessCb(action="open", uid=u.id))] for u in pending]
    rows += [
        [_btn(f"{u.full_name} · {t['role_' + u.role.value]}", AccessCb(action="open", uid=u.id))]
        for u in active
    ]
    return _kb(rows)


def user_card_kb(t: dict[str, str], target: User, is_self: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not is_self:
        for r in ROLE_ORDER:
            mark = "✅ " if target.role == r else ""
            rows.append([_btn(f"{mark}{t['role_' + r.value]}",
                              AccessCb(action="set", uid=target.id, role=r.value))])
        rows.append([_btn(t["btn_delete"], AccessCb(action="del", uid=target.id))])
    rows.append([_btn(t["back"], AccessCb(action="list"))])
    return _kb(rows)


def confirm_delete_kb(t: dict[str, str], uid: int) -> InlineKeyboardMarkup:
    return _kb([
        [_btn(t["btn_yes_delete"], AccessCb(action="delok", uid=uid))],
        [_btn(t["btn_cancel"], AccessCb(action="open", uid=uid))],
    ])


def review_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["btn_review"], AccessCb(action="list"))]])


# ---------- Yo'qlama ----------
def attendance_kb(t: dict[str, str], draft: AttendanceDraft) -> InlineKeyboardMarkup:
    icons = {None: "⬜", PLUS: "✅", MINUS: "❌", EXCUSED: "🟡"}
    rows = [
        [_btn(f"{icons[m]} {name}", AttCb(action="t", idx=i))]
        for i, (name, m) in enumerate(zip(draft.names, draft.marks))
    ]
    rows.append([_btn(t["att_btn_all"], AttCb(action="all"))])
    rows.append([_btn(t["att_btn_save"], AttCb(action="save"))])
    return _kb(rows)


def attendance_send_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([
        [_btn(t["att_btn_send"], AttCb(action="send"))],
        [_btn(t["att_btn_edit"], AttCb(action="edit"))],
    ])


def attendance_edit_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["att_btn_edit"], AttCb(action="edit"))]])


def attendance_start_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["att_btn_start"], AttCb(action="open"))]])


def view_menu_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([
        [_btn(t["view_btn_today"], ViewCb(action="today"))],
        [_btn(t["view_btn_week"], ViewCb(action="week"))],
        [_btn(t["view_btn_month"], ViewCb(action="month"))],
    ])


def attendance_menu_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([
        [_btn(t["att_btn_today"], AttCb(action="open"))],
        [_btn(t["att_btn_past"], AttCb(action="past", idx=0))],
    ])


def attendance_calendar_kb(t: dict[str, str], month_label: str, weekday_labels: list[str],
                           weeks: list[list[tuple[str, str, int]]],
                           prev_ym: int | None, next_ym: int | None) -> InlineKeyboardMarkup:
    """Oylik kalendar. weeks: haftalar, har biri 7 ta (matn, amal, qiymat). Amal: day | noop."""
    rows = [
        [
            _btn("◀️", AttCb(action="cal", idx=prev_ym)) if prev_ym else _btn("·", AttCb(action="noop")),
            _btn(month_label, AttCb(action="noop")),
            _btn("▶️", AttCb(action="cal", idx=next_ym)) if next_ym else _btn("·", AttCb(action="noop")),
        ],
        [_btn(w, AttCb(action="noop")) for w in weekday_labels],
    ]
    for week in weeks:
        rows.append([_btn(text, AttCb(action=action, idx=idx)) for text, action, idx in week])
    rows.append([_btn(t["back"], AttCb(action="menu"))])
    return _kb(rows)
