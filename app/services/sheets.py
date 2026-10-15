import asyncio
from typing import Any

import gspread

from app.config import Settings


class SheetsClient:
    """Google Sheets bilan ishlaydi. gspread sinxron, shuning uchun
    chaqiruvlar alohida oqimda bajariladi va bot qotib qolmaydi."""

    def __init__(self, settings: Settings) -> None:
        gc = gspread.service_account(filename=str(settings.credentials_file))
        spreadsheet = gc.open(settings.spreadsheet_name)
        self._worksheet = spreadsheet.worksheet(settings.worksheet_name)

    async def get_records(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._worksheet.get_all_records)
