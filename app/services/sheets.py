import asyncio
import threading
from typing import Any, Callable

import gspread

from app.config import Settings


class SheetsClient:
    """Google Sheets bilan ishlaydi. gspread sinxron, shuning uchun barcha
    chaqiruvlar alohida oqimda, bitta qulf (lock) ostida bajariladi."""

    def __init__(self, settings: Settings) -> None:
        gc = gspread.service_account(filename=str(settings.credentials_file))
        self._spreadsheet = gc.open(settings.spreadsheet_name)
        self._worksheet = self._spreadsheet.worksheet(settings.worksheet_name)
        self._lock = threading.Lock()

    @property
    def spreadsheet(self):
        return self._spreadsheet

    def _locked(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
        with self._lock:
            return fn(*args, **kwargs)

    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._locked, fn, args, kwargs)

    async def get_records(self) -> list[dict[str, Any]]:
        return await self.run(self._worksheet.get_all_records)
