import math
from typing import Sequence, TypeVar

T = TypeVar("T")


def paginate(items: Sequence[T], page: int, size: int) -> tuple[Sequence[T], int, int]:
    """(sahifadagi elementlar, to'g'rilangan sahifa raqami, jami sahifalar) qaytaradi."""
    total_pages = max(1, math.ceil(len(items) / size))
    page = min(max(page, 0), total_pages - 1)
    start = page * size
    return items[start:start + size], page, total_pages
