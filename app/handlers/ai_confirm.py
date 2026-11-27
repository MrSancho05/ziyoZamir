"""🤖 AI qoralamalarini tasdiqlash: «✅ Tasdiqlash» / «❌ Bekor qilish».

Faqat qoralamani yaratgan foydalanuvchi va faqat yozish huquqi bor rolda (Admin / Sklad nazoratchisi, faol rol
bo'yicha) bajariladi. Yozish botning mavjud funksiyalari orqali: Table chegaralari, formulalar, kesh, kunlik
kirim/chiqim hisobi (flows_store), kam qoldiq ogohlantirishi va narxsiz kirimlar nazorati saqlanadi."""
from __future__ import annotations

import logging
from datetime import date, datetime
from html import escape
from typing import Any, Dict, Optional

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.core.permissions import Permission, has_permission
from app.handlers.warehouse import _resolve_unpriced, _send_low_alert, _track_inflow
from app.keyboards.callbacks import AiCb
from app.models import User
from app.services.ai_agent import WRITE_ROLES, Draft
from app.services.warehouse import (ADJ_IN_NOTE, ADJ_OUT_NOTE, DuplicateError, InflowItem, NotFoundError,
                                    OutflowItem)
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)
router = Router(name="ai_confirm")

# qoralama turi -> kerakli ruxsat (permissions.py)
KIND_PERMISSION = {
    "inflow": Permission.MANAGE_WAREHOUSE, "outflow": Permission.MANAGE_WAREHOUSE,
    "product": Permission.MANAGE_WAREHOUSE, "adjust": Permission.MANAGE_WAREHOUSE,
    "price": Permission.MANAGE_WAREHOUSE, "other": Permission.MANAGE_WAREHOUSE,
    "shop": Permission.MARK_EXPENSES,
}


class DraftFailed(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def confirm_kb(t: Dict[str, str], draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t["ai_btn_ok"], callback_data=AiCb(a="ok", d=draft_id).pack()),
        InlineKeyboardButton(text=t["ai_btn_no"], callback_data=AiCb(a="no", d=draft_id).pack())]])


def _n(x: Any) -> str:
    s = f"{round(float(x or 0), 2):,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


def allowed(user: User, draft: Draft) -> bool:
    perm = KIND_PERMISSION.get(draft.kind)
    return user.role in WRITE_ROLES and perm is not None and has_permission(user.role, perm)


async def _after_warehouse(names, warehouse, flows, settings, bot, repo, user) -> None:
    """Kunlik kirim/chiqim hisobi va kam qoldiq ogohlantirishi (savatni saqlagandagi kabi)."""
    try:
        await flows.commit(datetime.now(settings.timezone).date())
    except Exception:
        logger.exception("flows_store ni yangilab bo'lmadi")
    try:
        low = await warehouse.low_stock(names)
    except Exception:
        logger.exception("Qoldiqni tekshirib bo'lmadi")
        low = []
    if low:
        await _send_low_alert(bot, repo, user, low)


async def execute(draft: Draft, t: Dict[str, str], user: User, warehouse, flows, shop, pending,
                  settings: Settings, bot: Bot, repo: UserRepository) -> str:
    """Qoralamani bajaradi; natija matni (Bajarildi …). Xato — DraftFailed(matn)."""
    p = draft.payload
    kind = draft.kind
    if kind in ("inflow", "outflow", "adjust"):
        product = await warehouse.product(p["name"], force=True)
        if product is None:
            raise DraftFailed(t["wh_not_in_catalog"])
    if kind == "inflow":
        items = [InflowItem(name=p["name"], unit=p["unit"], qty=p["qty"], price=p.get("price"),
                            supplier=p["supplier"], boxes=p.get("boxes") or 0.0, pack=p.get("pack") or 1.0)]
        rows = await warehouse.save_inflow(items)
        await _track_inflow(warehouse, items, rows)
        await _after_warehouse([p["name"]], warehouse, flows, settings, bot, repo, user)
        return t["ai_done_inflow"].format(tab=escape(warehouse.inflow_tab), rows=", ".join(map(str, rows)))
    if kind == "outflow":
        if p["qty"] > product.stock + 1e-9:
            raise DraftFailed(t["ai_fail_stock"].format(stock=_n(product.stock), unit=escape(product.unit)))
        await warehouse.save_outflow([OutflowItem(name=p["name"], unit=p["unit"], qty=p["qty"],
                                                  purpose=p.get("purpose", ""), recipient=p["recipient"],
                                                  stock_before=product.stock)])
        await _after_warehouse([p["name"]], warehouse, flows, settings, bot, repo, user)
        after = await warehouse.product(p["name"], force=True)
        return t["ai_done_outflow"].format(tab=escape(warehouse.outflow_tab),
                                           stock=_n(after.stock if after else product.stock - p["qty"]),
                                           unit=escape(product.unit))
    if kind == "adjust":
        delta = float(p["delta"])
        if product.stock + delta < -1e-9:
            raise DraftFailed(t["ai_fail_stock"].format(stock=_n(product.stock), unit=escape(product.unit)))
        if delta > 0:
            await warehouse.save_inflow([InflowItem(name=p["name"], unit=p["unit"], qty=delta,
                                                    price=product.avg_price or None, supplier=ADJ_IN_NOTE)])
        else:
            await warehouse.save_outflow([OutflowItem(name=p["name"], unit=p["unit"], qty=-delta, purpose="",
                                                      recipient=ADJ_OUT_NOTE, stock_before=product.stock)])
        await _after_warehouse([p["name"]], warehouse, flows, settings, bot, repo, user)
        after = await warehouse.product(p["name"], force=True)
        return t["ai_done_adjust"].format(stock=_n(after.stock if after else product.stock + delta),
                                          unit=escape(product.unit))
    if kind == "product":
        try:
            row = await warehouse.add_product(p["name"], p["category"], p["unit"], float(p["pack"]), float(p["min"]))
        except DuplicateError:
            raise DraftFailed(t["ai_fail_exists"].format(name=escape(p["name"])))
        if warehouse.product_log is not None:
            try:
                await warehouse.product_log.add(warehouse.today(), user.id, user.full_name, p["name"], p["category"],
                                                p["unit"], float(p["min"]))
            except Exception:
                logger.exception("Yangi mahsulot jurnaliga yozib bo'lmadi")
        return t["ai_done_product"].format(row=row)
    if kind == "price":
        try:
            total = await warehouse.set_price(int(p["row"]), p["name"], float(p["price"]))
        except NotFoundError:
            raise DraftFailed(t["ai_fail_moved"])
        await _resolve_unpriced(warehouse, {"name": p["name"], "qty": p["qty"], "iso": p.get("iso", "")})
        return t["ai_done_price"].format(total=_n(total))
    if kind == "shop":
        from app.services.attendance import LayoutError, TemplateMissingError
        from app.services.shop import NoBlockError
        day = date.fromisoformat(p["iso"])
        if pending is not None and await pending.is_sent("shop", day.isoformat()) is True:
            raise DraftFailed(t["ai_fail_sent"])
        today = datetime.now(settings.timezone).date()
        try:
            await shop.set_day(day, int(p["amount"]), (day.year, day.month) == (today.year, today.month))
        except TemplateMissingError as exc:
            raise DraftFailed(t["shop_no_template"].format(tab=escape(exc.tab)))
        except LayoutError:
            raise DraftFailed(t["shop_layout_error"])
        except NoBlockError:
            raise DraftFailed(t["shop_no_block"].format(date=day.strftime("%d.%m.%Y")))
        if pending is not None:
            await pending.ready("shop", day.isoformat(), user.id, int(datetime.now(settings.timezone).timestamp()))
        return t["ai_done_shop"].format(month=_n(await shop.month_total_until(day)))
    if kind == "other":
        other = getattr(warehouse, "other", None)
        if other is None:
            raise DraftFailed(t["ai_fail_generic"])
        await other.add(date.fromisoformat(p["iso"]), p["category"], p["description"], float(p["amount"]),
                        user.full_name)
        return t["ai_done_other"]
    raise DraftFailed(t["ai_fail_generic"])


@router.callback_query(AiCb.filter())
async def on_draft(callback: CallbackQuery, callback_data: AiCb, t: Dict[str, str], user: User,
                   warehouse=None, flows=None, shop=None, pending=None, settings: Optional[Settings] = None,
                   bot: Optional[Bot] = None, repo: Optional[UserRepository] = None, ai_agent=None) -> None:
    store = getattr(ai_agent, "drafts", None)
    draft = store.get(callback_data.d) if store is not None else None
    if draft is None:
        await callback.answer(t["ai_draft_expired"], show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    if draft.user_id != user.id:
        await callback.answer(t["ai_draft_foreign"], show_alert=True)
        return
    card = draft.card
    if callback_data.a != "ok":
        store.take(draft.id)
        await callback.answer()
        await callback.message.edit_text(f"{card}\n\n{t['ai_cancelled']}")
        return
    if not allowed(user, draft):                   # 🎭 rol almashtirilgan bo'lsa ham — faol rol bo'yicha
        await callback.answer(t["ai_draft_denied"], show_alert=True)
        return
    if store.take(draft.id) is None:               # ikki marta bosish — ikkinchisi bajarilmaydi
        await callback.answer(t["ai_draft_expired"], show_alert=True)
        return
    await callback.answer("⏳")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        detail = await execute(draft, t, user, warehouse, flows, shop, pending, settings, bot, repo)
        result = t["ai_done"].format(detail=detail)
    except DraftFailed as e:
        result = e.text
    except Exception:
        logger.exception("AI qoralamasini bajarib bo'lmadi: %s", draft.kind)
        result = t["ai_fail_generic"]
    await callback.message.edit_text(f"{card}\n\n{result}")
