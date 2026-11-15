from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.permissions import ROLE_ORDER, Role
from app.keyboards.callbacks import (
    AccessCb,
    AttCb,
    DownloadCb,
    InspectCb,
    LangCb,
    MenuCb,
    MyCb,
    PageCb,
    ProductCb,
    ShopCb,
    TopCb,
)
from app.models import AttendanceDraft, User
from app.services.attendance_grid import EXCUSED, MINUS, PLUS
from app.utils.periods import (
    AUTUMN,
    SPRING,
    SUMMER,
    TF_DAILY,
    TF_MONTHLY,
    TF_SEASONAL,
    TF_WEEKLY,
    TF_YEARLY,
    WINTER,
)

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


def inspect_menu_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    """"Mahsulotlarni tekshirish": Barcha mahsulotlar / Tanqis mahsulotlar."""
    return _kb([
        [_btn(t["btn_all_products"], ProductCb(kind="all"))],
        [_btn(t["btn_deficit_products"], ProductCb(kind="def"))],
    ])


def deficit_menu_kb(t: dict[str, str], can_download: bool, kind: str = "def") -> InlineKeyboardMarkup:
    """Ro'yxatni ko'rish hammaga; yuklab olish tugmasi faqat ruxsati borlarga."""
    rows = [[_btn(t["btn_view"], PageCb(page=0, kind=kind))]]
    if can_download:
        rows.append([_btn(t["btn_download"], MenuCb(action="download", kind=kind))])
    rows.append([_btn(t["back"], ProductCb(kind="menu"))])
    return _kb(rows)


def download_kb(t: dict[str, str], kind: str = "def") -> InlineKeyboardMarkup:
    return _kb([
        [
            _btn(t["pdf_btn"], DownloadCb(fmt="pdf", kind=kind)),
            _btn(t["csv_btn"], DownloadCb(fmt="csv", kind=kind)),
        ],
        [_btn(t["back"], MenuCb(action="back", kind=kind))],
    ])


def pagination_kb(page: int, total_pages: int, t: dict[str, str], kind: str = "def") -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(_btn(t["btn_prev"], PageCb(page=page - 1, kind=kind)))
    nav.append(_btn(f"📄 {page + 1}/{total_pages}", MenuCb(action="noop", kind=kind)))
    if page < total_pages - 1:
        nav.append(_btn(t["btn_next"], PageCb(page=page + 1, kind=kind)))

    jumps = []
    if page > 0:
        jumps.append(_btn(t["btn_first"], PageCb(page=0, kind=kind)))
    if page < total_pages - 1:
        jumps.append(_btn(t["btn_last"], PageCb(page=total_pages - 1, kind=kind)))

    rows = [nav]
    if jumps:
        rows.append(jumps)
    rows.append([_btn(t["back_to_main"], MenuCb(action="back", kind=kind))])
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
        if target.role == Role.WORKER:
            rows.append([_btn(t["btn_link"], AccessCb(action="link", uid=target.id))])
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
def attendance_kb(t: dict[str, str], draft: AttendanceDraft, show_send: bool = True) -> InlineKeyboardMarkup:
    """Yo'qlama oynasi. Belgilar darhol saqlanadi, shuning uchun «Saqlash» tugmasi yo'q.
    «Bossga yuborish» faqat shu kun hisoboti hali yuborilmagan bo'lsa ko'rinadi."""
    icons = {None: "⬜", PLUS: "✅", MINUS: "❌", EXCUSED: "🟡"}
    rows = [
        [_btn(f"{icons[m]} {name}", AttCb(action="t", idx=i))]
        for i, (name, m) in enumerate(zip(draft.names, draft.marks))
    ]
    rows.append([_btn(t["att_btn_all"], AttCb(action="all"))])
    if show_send:
        rows.append([_btn(t["att_btn_send"], AttCb(action="send"))])
    rows.append([_btn(t["btn_cancel"], AttCb(action="cancel"))])
    return _kb(rows)


def attendance_start_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["att_btn_start"], AttCb(action="open"))]])


# ---------- Boss/Admin tahlili: davomat va do'kon xarajatlari ----------
def analytics_menu_kb(t: dict[str, str], kind: str) -> InlineKeyboardMarkup:
    """Davr tanlash: kunlik, haftalik, oylik, mavsumiy, yillik. kind: att | shop."""
    return _kb([
        [_btn(t[key], InspectCb(kind=kind, action="tf", tf=tf))]
        for key, tf in (("an_tf_daily", TF_DAILY), ("an_tf_weekly", TF_WEEKLY), ("an_tf_monthly", TF_MONTHLY),
                        ("an_tf_seasonal", TF_SEASONAL), ("an_tf_yearly", TF_YEARLY))
    ])


def analytics_season_kb(t: dict[str, str], kind: str) -> InlineKeyboardMarkup:
    def season(key: str, idx: int) -> InlineKeyboardButton:
        return _btn(t[key], InspectCb(kind=kind, action="go", tf=TF_SEASONAL, season=idx))

    return _kb([
        [season("an_season_winter", WINTER), season("an_season_spring", SPRING)],
        [season("an_season_summer", SUMMER), season("an_season_autumn", AUTUMN)],
        [_btn(t["back"], InspectCb(kind=kind, action="menu"))],
    ])


def analytics_result_kb(t: dict[str, str], kind: str, tf: str, season: int, ymd: int,
                        showing: str) -> InlineKeyboardMarkup:
    """Natija xabari ostidagi yagona tugma: qisqacha ko'rinishda "Batafsil", batafsilda "Qisqacha".
    Bosilganda xabar o'sha joyda tahrirlanadi (yangi xabar yuborilmaydi)."""
    target, label = ("d", t["an_detail"]) if showing == "b" else ("b", t["an_brief"])
    return _kb([[_btn(label, InspectCb(kind=kind, action="tog", tf=tf, season=season, mode=target, ymd=ymd))]])


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


# ---------- Do'kon xarajatlari ----------
def shop_menu_kb(t: dict[str, str], today_entered: bool = False) -> InlineKeyboardMarkup:
    """Bugungi xarajat allaqachon kiritilgan bo'lsa, "kiritish" o'rniga "o'zgartirish" tugmasi chiqadi."""
    first = (_btn(t["shop_btn_adjust_today"], ShopCb(action="adjmenu")) if today_entered
             else _btn(t["shop_btn_today"], ShopCb(action="open")))
    return _kb([
        [first],
        [_btn(t["shop_btn_past"], ShopCb(action="past"))],
    ])


def shop_adjust_menu_kb(t: dict[str, str], ymd: int) -> InlineKeyboardMarkup:
    """Bitta birlashgan "Qo'shish yoki ayirish" tugmasi (alohida qo'shish/ayirish yo'q)."""
    return _kb([
        [_btn(t["shop_btn_add_sub"], ShopCb(action="adj", idx=ymd))],
        [_btn(t["back"], ShopCb(action="menu"))],
    ])


def shop_enter_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["shop_btn_enter"], ShopCb(action="open"))]])


def shop_cancel_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["shop_btn_cancel"], ShopCb(action="cancel"))]])


def shop_saved_kb(t: dict[str, str], ymd: int) -> InlineKeyboardMarkup:
    return _kb([
        [_btn(t["shop_btn_send"], ShopCb(action="send", idx=ymd))],
        [_btn(t["shop_btn_edit"], ShopCb(action="edit", idx=ymd))],
    ])


def shop_edit_kb(t: dict[str, str], ymd: int) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["shop_btn_edit"], ShopCb(action="edit", idx=ymd))]])


def shop_calendar_kb(t: dict[str, str], month_label: str, weekday_labels: list[str],
                     weeks: list[list[tuple[str, str, int]]],
                     prev_ym: int | None, next_ym: int | None) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn("◀️", ShopCb(action="cal", idx=prev_ym)) if prev_ym else _btn("·", ShopCb(action="noop")),
            _btn(month_label, ShopCb(action="noop")),
            _btn("▶️", ShopCb(action="cal", idx=next_ym)) if next_ym else _btn("·", ShopCb(action="noop")),
        ],
        [_btn(w, ShopCb(action="noop")) for w in weekday_labels],
    ]
    for week in weeks:
        rows.append([_btn(text, ShopCb(action=action, idx=idx)) for text, action, idx in week])
    rows.append([_btn(t["back"], ShopCb(action="menu"))])
    return _kb(rows)


# ---------- Ishchi: mening davomatim ----------
def my_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["my_btn_this"], MyCb(action="this")), _btn(t["my_btn_prev"], MyCb(action="prev"))]])


def link_names_kb(t: dict[str, str], uid: int, names: list[str]) -> InlineKeyboardMarkup:
    rows = [[_btn(name, AccessCb(action="linkset", uid=uid, role=str(i)))] for i, name in enumerate(names)]
    rows.append([_btn(t["back"], AccessCb(action="open", uid=uid))])
    return _kb(rows)


# ---------- Top mahsulotlar (faqat admin) ----------
def top_periods_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([
        [_btn(t["btn_top_30"], TopCb(period="30")), _btn(t["btn_top_90"], TopCb(period="90"))],
        [_btn(t["btn_top_all"], TopCb(period="all"))],
    ])
