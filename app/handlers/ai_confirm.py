"""✍️ Qoralamalarni tasdiqlash (AI yordamchi va 🗂 menyu): «✅ Tasdiqlash» / «❌ Bekor qilish».

Faqat qoralamani yaratgan foydalanuvchi va faqat kerakli ruxsati bor faol rolda bajariladi:
📤 Chiqim — MAKE_OUTFLOW (Admin, Sklad, Usta); do'kon xarajati — MARK_EXPENSES; qolganlari — MANAGE_WAREHOUSE.
Yozish botning mavjud funksiyalari orqali (Table chegaralari, formulalar). Har bir amaldan keyin:
kunlik kirim/chiqim hisobi (flows_store) shu kun uchun qayta yoziladi, narxsiz kirimlar jadvali (`unpriced_items`)
varaq bilan moslanadi (o'chirilgan qatordan keyingi `sheet_row` lar ham), kam qoldiq ogohlantirishi, audit
jurnali va barcha keshlar (cache_bus: ombor, «Прочие расходы», Jonli Dashboard) tozalanadi."""
from __future__ import annotations

import logging
from datetime import date, datetime
from html import escape
from typing import Any, Dict, List, Optional

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.core.permissions import Permission, has_permission
from app.handlers.warehouse import _reconcile_unpriced, _resolve_unpriced, _send_low_alert, _track_inflow
from app.keyboards.callbacks import AiCb
from app.models import User
from app.services import cache_bus
from app.services.drafts import DRAFTS, Draft
from app.services.warehouse import (ADJ_IN_NOTE, ADJ_OUT_NOTE, RECIPIENT, DuplicateError, InflowItem,
                                    LastProductError, NotFoundError, OutflowItem)
from app.storage.users import UserRepository

logger = logging.getLogger(__name__)
router = Router(name="ai_confirm")

W, E = Permission.MANAGE_WAREHOUSE, Permission.MARK_EXPENSES
KIND_PERMISSION = {
    "inflow": W, "outflow": Permission.MAKE_OUTFLOW, "product": W, "adjust": W, "price": W, "other": W,
    "del_in": W, "del_out": W, "edit_in": W, "edit_out": W, "del_other": W, "edit_other": W,
    "rename": W, "del_product": W, "dir_add": W, "dir_del": W, "shop": E, "clear_shop": E,
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


def _neg(t: Dict[str, str], product, qty: float) -> str:
    from app.services.i18n_names import localize_product_name, localize_unit
    lang = t.get("lang_code", "uz")
    return t["neg_stock_block"].format(product=escape(localize_product_name(product.name, lang)),
                                       stock=_n(max(product.stock, 0)), unit=escape(localize_unit(product.unit, lang)),
                                       qty=_n(qty))


def allowed(user: User, draft: Draft) -> bool:
    perm = KIND_PERMISSION.get(draft.kind)
    return perm is not None and has_permission(user.role, perm)


async def _sync_days(days: List[Optional[date]], flows, settings: Settings) -> None:
    """Saqlangan kunlik hisob (Dashboard, hisobotlar): bugun — commit, o'tgan kunlar — resync_day."""
    if flows is None:
        return
    today = datetime.now(settings.timezone).date()
    for d in {d for d in days if d is not None}:
        try:
            if d == today:
                await flows.commit(today)
            elif d < today:
                await flows.resync_day(d)
        except Exception:
            logger.exception("flows_store ni yangilab bo'lmadi: %s", d)


async def _low_alert(names, warehouse, bot, repo, user) -> None:
    try:
        low = await warehouse.low_stock(names)
    except Exception:
        logger.exception("Qoldiqni tekshirib bo'lmadi")
        low = []
    if low and bot is not None and repo is not None:
        await _send_low_alert(bot, repo, user, low)


async def _shop_audit_notice(bot, repo, user: User, day: date, old: int, new: int, reason: str) -> int:
    from app.services.boss_reports import send_to_bosses
    from app.services.shop_audit import format_audit_notice

    def build(bt: Dict[str, str], lang: str) -> str:
        return format_audit_notice(bt, day, old, new, user.full_name, reason)

    try:
        return await send_to_bosses(bot, repo, build)
    except Exception:
        logger.exception("Tuzatish xabarini yuborib bo'lmadi")
        return 0


def _day(iso: str) -> Optional[date]:
    try:
        return date.fromisoformat(iso) if iso else None
    except ValueError:
        return None


async def execute(draft: Draft, t: Dict[str, str], user: User, warehouse, flows, shop, pending,
                  settings: Settings, bot: Optional[Bot], repo: Optional[UserRepository]) -> str:
    """Qoralamani bajaradi; natija matni. Xato — DraftFailed(matn)."""
    p = draft.payload
    kind = draft.kind
    today = datetime.now(settings.timezone).date()
    product = None
    if kind in ("inflow", "outflow", "adjust"):
        product = await warehouse.product(p["name"], force=True)
        if product is None:
            raise DraftFailed(t["wh_not_in_catalog"])

    if kind == "inflow":
        items = [InflowItem(name=p["name"], unit=p["unit"], qty=p["qty"], price=p.get("price"),
                            supplier=p["supplier"], boxes=p.get("boxes") or 0.0, pack=p.get("pack") or 1.0)]
        rows = await warehouse.save_inflow(items)
        await _track_inflow(warehouse, items, rows)
        await _sync_days([today], flows, settings)
        await _low_alert([p["name"]], warehouse, bot, repo, user)
        return t["ai_done_inflow"].format(tab=escape(warehouse.inflow_tab), rows=", ".join(map(str, rows)))

    if kind == "outflow":
        if p["qty"] > product.stock + 1e-9:
            raise DraftFailed(_neg(t, product, abs(float(p.get("qty") or p.get("delta") or 0))))
        await warehouse.save_outflow([OutflowItem(name=p["name"], unit=p["unit"], qty=p["qty"],
                                                  purpose=p.get("purpose", ""), recipient=p["recipient"],
                                                  stock_before=product.stock)])
        await warehouse.remember_name(RECIPIENT, p["recipient"])        # yangi usta Справочник ga
        await _sync_days([today], flows, settings)
        await _low_alert([p["name"]], warehouse, bot, repo, user)
        after = await warehouse.product(p["name"], force=True)
        return t["ai_done_outflow"].format(tab=escape(warehouse.outflow_tab),
                                           stock=_n(after.stock if after else product.stock - p["qty"]),
                                           unit=escape(product.unit))

    if kind == "adjust":
        delta = float(p["delta"])
        if product.stock + delta < -1e-9:
            raise DraftFailed(_neg(t, product, abs(float(p.get("qty") or p.get("delta") or 0))))
        if delta > 0:
            await warehouse.save_inflow([InflowItem(name=p["name"], unit=p["unit"], qty=delta,
                                                    price=product.avg_price or None, supplier=ADJ_IN_NOTE)])
        else:
            await warehouse.save_outflow([OutflowItem(name=p["name"], unit=p["unit"], qty=-delta, purpose="",
                                                      recipient=ADJ_OUT_NOTE, stock_before=product.stock)])
        await _sync_days([today], flows, settings)
        await _low_alert([p["name"]], warehouse, bot, repo, user)
        after = await warehouse.product(p["name"], force=True)
        return t["ai_done_adjust"].format(stock=_n(after.stock if after else product.stock + delta),
                                          unit=escape(product.unit))

    if kind in ("del_in", "del_out", "edit_in", "edit_out"):
        expect = p["expect"]
        old_day = _day(expect.get("iso", ""))
        if kind in ("del_in", "edit_in"):
            # kirimni o'chirish / kamaytirish: qoldiq 0 dan pastga tushmasin (keshsiz o'qish)
            prod = await warehouse.product(p["name"], force=True)
            old_qty = float(expect.get("qty") or 0)
            fields = p.get("fields") or {}
            if kind == "del_in":
                removed = old_qty                                   # butun kirim olib tashlanadi
            else:
                removed = old_qty - float(fields["qty"]) if "qty" in fields else 0.0
            if prod is not None and removed > 0 and prod.stock - removed < -1e-9:
                raise DraftFailed(_neg(t, prod, removed))
        try:
            if kind.startswith("del"):
                await warehouse.delete_entry(expect)
                detail = t["ai_done_deleted"]
            else:
                fields = dict(p["fields"])
                if kind == "edit_out" and "qty" in fields:
                    prod = await warehouse.product(p["name"], force=True)
                    extra = float(fields["qty"]) - float(expect.get("qty") or 0)
                    if prod is not None and extra > prod.stock + 1e-9:
                        raise DraftFailed(_neg(t, prod, extra))
                await warehouse.update_entry(expect, fields)
                detail = t["ai_done_edited"]
        except NotFoundError:
            raise DraftFailed(t["ai_fail_moved"])
        if kind.endswith("_in"):                     # narxsiz kirimlar: o'chgan / siljigan qatorlar
            try:
                await _reconcile_unpriced(warehouse, await warehouse.unpriced())
            except Exception:
                logger.exception("Narxsiz kirimlarni moslab bo'lmadi")
        new_day = _day((p.get("fields") or {}).get("iso", ""))
        await _sync_days([old_day, new_day], flows, settings)
        await _low_alert([p["name"]], warehouse, bot, repo, user)
        return detail

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

    if kind == "rename":
        try:
            await warehouse.rename_product(p["old"], p["new"])
        except DuplicateError:
            raise DraftFailed(t["ai_fail_exists"].format(name=escape(p["new"])))
        except NotFoundError:
            raise DraftFailed(t["wh_not_in_catalog"])
        if warehouse.product_log is not None:
            await warehouse.product_log.rename(p["old"], p["new"])
        try:
            await _reconcile_unpriced(warehouse, await warehouse.unpriced())
        except Exception:
            logger.exception("Narxsiz kirimlarni moslab bo'lmadi")
        return t["ai_done_rename"]

    if kind == "del_product":
        try:
            await warehouse.delete_product(p["name"])
        except NotFoundError:
            raise DraftFailed(t["wh_not_in_catalog"])
        except LastProductError:
            raise DraftFailed(t["ai_fail_last_product"])
        if warehouse.product_log is not None:
            await warehouse.product_log.delete(p["name"])
        return t["ai_done_del_product"]

    if kind in ("dir_add", "dir_del"):
        if kind == "dir_add":
            await warehouse.add_directory_value(p["dk"], p["value"])
        else:
            await warehouse.delete_directory_value(p["dk"], p["value"])
        return t["ai_done_dir"]

    if kind == "price":
        try:
            total = await warehouse.set_price(int(p["row"]), p["name"], float(p["price"]))
        except NotFoundError:
            raise DraftFailed(t["ai_fail_moved"])
        await _resolve_unpriced(warehouse, {"name": p["name"], "qty": p["qty"], "iso": p.get("iso", "")})
        d = _day(p.get("iso", ""))
        await _sync_days([d], flows, settings)
        return t["ai_done_price"].format(total=_n(total))

    if kind in ("shop", "clear_shop"):
        from app.services.attendance import LayoutError, TemplateMissingError
        from app.services.shop import NoBlockError, NotEnteredError
        day = date.fromisoformat(p["iso"])
        sent = pending is not None and await pending.is_sent("shop", day.isoformat()) is True
        if sent and not p.get("reason"):
            raise DraftFailed(t["ai_fail_sent"])          # yuborilgandan keyin — faqat sabab bilan
        try:
            if kind == "shop":
                await shop.set_day(day, int(p["amount"]), (day.year, day.month) == (today.year, today.month))
                old, new = int(p.get("old") or 0), int(p["amount"])
            else:
                old, new = await shop.clear_day(day), 0
        except TemplateMissingError as exc:
            raise DraftFailed(t["shop_no_template"].format(tab=escape(exc.tab)))
        except LayoutError:
            raise DraftFailed(t["shop_layout_error"])
        except NoBlockError:
            raise DraftFailed(t["shop_no_block"].format(date=day.strftime("%d.%m.%Y")))
        except NotEnteredError:
            raise DraftFailed(t["ai_fail_moved"])
        if sent:
            # hisobot qayta yuborilmaydi — faqat ✏️ tuzatish xabari Boss va Adminga
            count = await _shop_audit_notice(bot, repo, user, day, old, new, p.get("reason", ""))
            return t["ai_done_shop_audit"].format(count=count)
        if kind == "shop" and pending is not None:
            await pending.ready("shop", day.isoformat(), user.id, int(datetime.now(settings.timezone).timestamp()))
        if kind == "clear_shop":
            return t["ai_done_shop_clear"]
        return t["ai_done_shop"].format(month=_n(await shop.month_total_until(day)))

    if kind in ("other", "del_other", "edit_other"):
        other = getattr(warehouse, "other", None)
        if other is None:
            raise DraftFailed(t["ai_fail_generic"])
        if kind == "other":
            await other.add(date.fromisoformat(p["iso"]), p["category"], p["description"], float(p["amount"]),
                            user.full_name)
            return t["ai_done_other"]
        try:
            if kind == "del_other":
                await other.delete(p["expect"])
                return t["ai_done_other_del"]
            await other.update(p["expect"], p["fields"])
            return t["ai_done_other_edit"]
        except LookupError:
            raise DraftFailed(t["ai_fail_moved"])
    raise DraftFailed(t["ai_fail_generic"])


@router.callback_query(AiCb.filter())
async def on_draft(callback: CallbackQuery, callback_data: AiCb, t: Dict[str, str], user: User,
                   warehouse=None, flows=None, shop=None, pending=None, settings: Optional[Settings] = None,
                   bot: Optional[Bot] = None, repo: Optional[UserRepository] = None, ai_agent=None,
                   audit=None) -> None:
    store = getattr(ai_agent, "drafts", None) or DRAFTS
    draft = store.get(callback_data.d)
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
    ok = False
    try:
        detail = await execute(draft, t, user, warehouse, flows, shop, pending, settings, bot, repo)
        result = t["ai_done"].format(detail=detail)
        ok = True
    except DraftFailed as e:
        result = e.text
    except Exception:
        logger.exception("Qoralamani bajarib bo'lmadi: %s", draft.kind)
        result = t["ai_fail_generic"]
    finally:
        cache_bus.invalidate_all()                 # ombor, «Прочие расходы», Dashboard — darhol yangi raqamlar
    if ok and audit is not None:
        try:
            await audit.add(user.id, user.full_name, draft.kind, str(draft.payload.get("name")
                                                                     or draft.payload.get("iso") or ""),
                            draft.payload, source=draft.source)
        except Exception:
            logger.exception("Audit jurnaliga yozib bo'lmadi")
    await callback.message.edit_text(f"{card}\n\n{result}")
