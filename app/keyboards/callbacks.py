from aiogram.filters.callback_data import CallbackData


class LangCb(CallbackData, prefix="lang"):
    code: str


class ProductCb(CallbackData, prefix="prod"):
    kind: str  # "menu" (Barcha / Tanqis tanlash) | "all" (barcha mahsulotlar) | "def" (tanqis mahsulotlar)


class PageCb(CallbackData, prefix="page"):
    page: int
    kind: str = "def"  # "all" | "def"


class MenuCb(CallbackData, prefix="menu"):
    action: str  # "download" | "back" | "noop"
    kind: str = "def"  # "all" | "def"


class DownloadCb(CallbackData, prefix="dl"):
    fmt: str  # "pdf" | "csv"
    kind: str = "def"  # "all" | "def"


class AccessCb(CallbackData, prefix="acc"):
    action: str  # list | open | approve | reject | set | del | delok
    uid: int = 0
    role: str = ""


class AttCb(CallbackData, prefix="att"):
    action: str  # t (toggle) | all | send | cancel | open | past | cal | day | menu | noop
    idx: int = 0


class ViewCb(CallbackData, prefix="view"):
    action: str  # today | week | month


class InspectCb(CallbackData, prefix="ins"):
    """Boss/Admin tahlili (davomat va do'kon xarajati): davr tanlash va "Batafsil" <-> "Qisqacha"."""
    kind: str          # att (davomat) | shop (do'kon xarajati)
    action: str        # menu (davr menyusi) | tf (davr tanlandi) | go (mavsum tanlandi) | tog (Batafsil <-> Qisqacha)
    tf: str = ""       # d (kunlik) | w (haftalik) | m (oylik) | s (mavsumiy) | y (yillik)
    season: int = 0    # 0 qish | 1 bahor | 2 yoz | 3 kuz (faqat tf == "s")
    mode: str = "b"    # tog: ko'rsatiladigan ko'rinish: b (qisqacha) | d (batafsil)
    ymd: int = 0       # tahlil olingan sana (YYYYMMDD): tugma keyin bosilsa ham davr o'zgarmasligi uchun


class ShopCb(CallbackData, prefix="shop"):
    action: str  # menu | open | past | cal | day | send | edit | adjmenu | adj | cancel | noop
    idx: int = 0


class MyCb(CallbackData, prefix="my"):
    action: str  # this | prev


class TopCb(CallbackData, prefix="top"):
    """"Top mahsulotlar" (faqat admin): davr tanlash."""
    period: str   # 30 | 90 | all
