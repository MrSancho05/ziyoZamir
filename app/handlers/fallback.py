from aiogram import Router
from aiogram.types import CallbackQuery

router = Router(name="fallback")


@router.callback_query()
async def denied(callback: CallbackQuery, t: dict[str, str]) -> None:
    """Hech bir handler olmagan (odatda ruxsat yetmagan) tugma bosilsa."""
    await callback.answer(t["no_permission"], show_alert=True)
