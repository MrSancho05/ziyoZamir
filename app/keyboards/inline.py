from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.keyboards.webapp import dashboard_rows

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

ASSIGNABLE_ON_REQUEST = (Role.BOSS, Role.STOREKEEPER, Role.MASTER, Role.WORKER)


def _btn(text: str, cb) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _grid(buttons: list[InlineKeyboardButton], nav: list[list[InlineKeyboardButton]] | None = None,
          cols: int = 2) -> InlineKeyboardMarkup:
    """Asosiy tugmalar 2 ustunli to'r (adjust(2)); navigatsiya (Orqaga / Bekor qilish) pastda to'liq qatorda."""
    kb = InlineKeyboardBuilder()
    for b in buttons:
        kb.add(b)
    kb.adjust(cols)
    for row in nav or []:
        if row:
            kb.row(*row)
    return kb.as_markup()


def _short(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def language_kb() -> InlineKeyboardMarkup:
    """3 ta til: O'zbekcha (lotin), Ўзбекча (кирилл), Русский."""
    return _kb([[_btn("🇺🇿 O'zbekcha (Lotin)", LangCb(code="uz"))],
                [_btn("🇺🇿 Ўзбекча (Кирилл)", LangCb(code="cyrl"))],
                [_btn("🇷🇺 Русский", LangCb(code="ru"))]])


def inspect_menu_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    """"Mahsulotlarni tekshirish": Barcha mahsulotlar / Tanqis mahsulotlar."""
    return _grid([_btn(t["btn_all_products"], ProductCb(kind="all")),
                  _btn(t["btn_deficit_products"], ProductCb(kind="def"))])


def deficit_menu_kb(t: dict[str, str], can_download: bool, kind: str = "def",
                    dashboard: bool = False) -> InlineKeyboardMarkup:
    """Ro'yxatni ko'rish hammaga; yuklab olish tugmasi faqat ruxsati borlarga; 📊 Dashboard — Boss/Admin/Sklad."""
    buttons = [_btn(t["btn_view"], PageCb(page=0, kind=kind))]
    if can_download:
        buttons.append(_btn(t["btn_download_file"], MenuCb(action="download", kind=kind)))
    return _grid(buttons, [[_btn(t["back"], ProductCb(kind="menu"))]] + (dashboard_rows(t) if dashboard else []))


def download_kb(t: dict[str, str], kind: str = "def") -> InlineKeyboardMarkup:
    """📊 Excel (.xlsx) va 📄 PDF. (Eski xabarlardagi CSV tugmasi ham ishlayveradi.)"""
    return _kb([
        [
            _btn(t["xlsx_btn"], DownloadCb(fmt="xlsx", kind=kind)),
            _btn(t["pdf_btn2"], DownloadCb(fmt="pdf", kind=kind)),
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
    buttons = [_btn(t[f"role_{r.value}"], AccessCb(action="approve", uid=uid, role=r.value))
               for r in ASSIGNABLE_ON_REQUEST]
    buttons.append(_btn(t["btn_reject"], AccessCb(action="reject", uid=uid)))
    nav = [[_btn(t["back"], AccessCb(action="list"))]] if with_back else []
    return _grid(buttons, nav)


def users_list_kb(t: dict[str, str], pending: list[User], active: list[User]) -> InlineKeyboardMarkup:
    buttons = [_btn(f"🆕 {_short(u.full_name)}", AccessCb(action="open", uid=u.id)) for u in pending]
    buttons += [_btn(_short(f"{u.full_name} · {t['role_' + u.role.value]}", 28), AccessCb(action="open", uid=u.id))
                for u in active]
    return _grid(buttons)


def user_card_kb(t: dict[str, str], target: User, is_self: bool) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if not is_self:
        for r in ROLE_ORDER:
            mark = "✅ " if target.role == r else ""
            buttons.append(_btn(f"{mark}{t['role_' + r.value]}",
                                AccessCb(action="set", uid=target.id, role=r.value)))
        if target.role == Role.WORKER:
            buttons.append(_btn(t["btn_link"], AccessCb(action="link", uid=target.id)))
        buttons.append(_btn(t["btn_delete"], AccessCb(action="del", uid=target.id)))
    return _grid(buttons, [[_btn(t["back"], AccessCb(action="list"))]])


def confirm_delete_kb(t: dict[str, str], uid: int) -> InlineKeyboardMarkup:
    return _grid([_btn(t["btn_yes_delete"], AccessCb(action="delok", uid=uid)),
                  _btn(t["btn_cancel"], AccessCb(action="open", uid=uid))])


def review_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["btn_review"], AccessCb(action="list"))]])


# ---------- Yo'qlama ----------
def attendance_kb(t: dict[str, str], draft: AttendanceDraft, show_send: bool = True) -> InlineKeyboardMarkup:
    """Yo'qlama oynasi. Belgilar darhol saqlanadi, shuning uchun «Saqlash» tugmasi yo'q.
    «Bossga yuborish» faqat shu kun hisoboti hali yuborilmagan bo'lsa ko'rinadi."""
    icons = {None: "⬜", PLUS: "✅", MINUS: "❌", EXCUSED: "🟡"}
    names = [_btn(f"{icons[m]} {_short(name, 20)}", AttCb(action="t", idx=i))
             for i, (name, m) in enumerate(zip(draft.names, draft.marks))]
    actions = [_btn(t["att_btn_all"], AttCb(action="all"))]
    if show_send:
        actions.append(_btn(t["att_btn_send"], AttCb(action="send")))
    kb = InlineKeyboardBuilder()
    for b in names:
        kb.add(b)
    kb.adjust(2)
    kb.row(*actions)
    kb.row(_btn(t["btn_cancel"], AttCb(action="cancel")))
    return kb.as_markup()


def attendance_start_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["att_btn_start"], AttCb(action="open"))]])


# ---------- Boss/Admin tahlili: davomat va do'kon xarajatlari ----------
def analytics_menu_kb(t: dict[str, str], kind: str) -> InlineKeyboardMarkup:
    """Davr tanlash: kunlik, haftalik, oylik, mavsumiy, yillik. kind: att | shop."""
    return _grid([
        _btn(t[key], InspectCb(kind=kind, action="tf", tf=tf))
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
    return _kb([[_btn(label, InspectCb(kind=kind, action="tog", tf=tf, season=season, mode=target, ymd=ymd))]]
               + dashboard_rows(t))


def attendance_menu_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _grid([_btn(t["att_btn_today"], AttCb(action="open")),
                  _btn(t["att_btn_past"], AttCb(action="past", idx=0))])


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
    return _grid([first, _btn(t["shop_btn_past"], ShopCb(action="past"))])


def shop_adjust_menu_kb(t: dict[str, str], ymd: int) -> InlineKeyboardMarkup:
    """Bitta birlashgan "Qo'shish yoki ayirish" tugmasi (alohida qo'shish/ayirish yo'q)."""
    return _grid([_btn(t["shop_btn_add_sub"], ShopCb(action="adj", idx=ymd))],
                 [[_btn(t["back"], ShopCb(action="menu"))]])


def shop_enter_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["shop_btn_enter"], ShopCb(action="open"))]])


def shop_cancel_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _kb([[_btn(t["shop_btn_cancel"], ShopCb(action="cancel"))]])


def shop_saved_kb(t: dict[str, str], ymd: int) -> InlineKeyboardMarkup:
    return _grid([_btn(t["shop_btn_send"], ShopCb(action="send", idx=ymd)),
                  _btn(t["shop_btn_edit"], ShopCb(action="edit", idx=ymd))])


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
    buttons = [_btn(_short(name), AccessCb(action="linkset", uid=uid, role=str(i))) for i, name in enumerate(names)]
    return _grid(buttons, [[_btn(t["back"], AccessCb(action="open", uid=uid))]])


# ---------- Top mahsulotlar (faqat admin) ----------
def top_periods_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    return _grid([_btn(t["btn_top_30"], TopCb(period="30")), _btn(t["btn_top_90"], TopCb(period="90")),
                  _btn(t["btn_top_all"], TopCb(period="all"))])


# ---------- Boss/Admin hisobotlari: joyida tahrirlanadigan tugmalar ----------
def report_toggle_kb(t: dict[str, str], kind: str, key: str, showing: str = "brief") -> InlineKeyboardMarkup:
    """Kunlik hisobot: 📄 Batafsil <-> 🔼 Qisqacha."""
    from app.keyboards.callbacks import RepCb
    if showing == "brief":
        return _kb([[_btn(t["rp_btn_detail"], RepCb(k=kind, p=key, s="detail"))]] + dashboard_rows(t))
    return _kb([[_btn(t["rp_btn_brief"], RepCb(k=kind, p=key, s="brief"))]] + dashboard_rows(t))


def digest_kb(t: dict[str, str], kind: str, key: str, showing: str = "brief") -> InlineKeyboardMarkup:
    """Haftalik / oylik yig'ma hisobot: bo'limlar batafsil + 🔼 Qisqacha (hammasi bitta xabarda)."""
    from app.keyboards.callbacks import RepCb
    buttons = [_btn(("• " if showing == s else "") + t[label], RepCb(k=kind, p=key, s=s))
               for s, label in (("shop", "rp_btn_shop"), ("wh", "rp_btn_wh"), ("att", "rp_btn_att"))]
    nav = [[_btn(t["rp_btn_brief"], RepCb(k=kind, p=key, s="brief"))]] if showing != "brief" else []
    return _grid(buttons, nav + dashboard_rows(t))


def role_pick_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    from app.keyboards.callbacks import RoleCb
    return _grid([_btn(t["role_boss"], RoleCb(r="boss")), _btn(t["role_storekeeper"], RoleCb(r="storekeeper")),
                  _btn("👷 " + t["role_master"], RoleCb(r="master")), _btn(t["role_worker"], RoleCb(r="worker"))],
                 [[_btn(t["btn_admin_back"], RoleCb(r="admin"))]])


def shop_reason_kb(t: dict[str, str]) -> InlineKeyboardMarkup:
    """Bossga yuborilgan kun tahrirlanganda: tuzatish sababi."""
    return _grid([_btn(t["shop_reason_typo"], ShopCb(action="rsn", idx=0)),
                  _btn(t["shop_reason_extra"], ShopCb(action="rsn", idx=1)),
                  _btn(t["shop_reason_return"], ShopCb(action="rsn", idx=2))],
                 [[_btn(t["shop_btn_cancel"], ShopCb(action="cancel"))]])
