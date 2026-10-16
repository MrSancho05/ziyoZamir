from aiogram.filters.callback_data import CallbackData


class LangCb(CallbackData, prefix="lang"):
    code: str


class PageCb(CallbackData, prefix="page"):
    page: int


class MenuCb(CallbackData, prefix="menu"):
    action: str  # "download" | "back" | "noop"


class DownloadCb(CallbackData, prefix="dl"):
    fmt: str  # "pdf" | "csv"


class AccessCb(CallbackData, prefix="acc"):
    action: str  # list | open | approve | reject | set | del | delok
    uid: int = 0
    role: str = ""


class AttCb(CallbackData, prefix="att"):
    action: str  # t (toggle) | all | save | send
    idx: int = 0
