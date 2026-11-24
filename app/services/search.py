"""Mahsulot nomini aqlli qidirish: kirill <-> lotin transliteratsiyasi + noaniq (fuzzy) moslik.

Masalan "samarez" -> "Саморез", "petla" -> "Петля", "akfks" -> "Акфикс" kabi xatolar ham topiladi.
Google Sheets ga bog'liq emas.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, List, Sequence

_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "j", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "s", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # o'zbek kirill harflari
    "ў": "o", "қ": "k", "ғ": "g", "ҳ": "x",
}

# lotin yozuvidagi o'xshash tovushlar bitta ko'rinishga keltiriladi
_LAT_FOLD = (
    ("o'", "o"), ("g'", "g"), ("oʻ", "o"), ("gʻ", "g"), ("‘", ""), ("’", ""), ("'", ""), ("`", ""),
    ("sh", "s"), ("ch", "c"), ("ts", "s"), ("kh", "x"), ("h", "x"), ("q", "k"), ("w", "v"),
    ("yu", "u"), ("ya", "a"), ("yo", "o"), ("ye", "e"), ("j", "j"), ("c", "s"), ("y", "i"),
)


def normalize(text: str) -> str:
    """Matnni yozuvdan qat'i nazar solishtirish uchun yagona lotin ko'rinishiga keltiradi."""
    s = str(text).strip().lower()
    s = "".join(_CYR2LAT.get(ch, ch) for ch in s)
    for a, b in _LAT_FOLD:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"(.)\1+", r"\1", s)          # takror harflar: "ss" -> "s"
    return s.strip()


def _compact(s: str) -> str:
    return s.replace(" ", "")


def _score(q: str, name: str) -> float:
    if not q or not name:
        return 0.0
    if q == name:
        return 1.0
    qc, nc = _compact(q), _compact(name)
    best = difflib.SequenceMatcher(None, qc, nc).ratio()
    if qc and qc in nc:
        best = max(best, 0.85 + 0.1 * len(qc) / max(len(nc), 1))
    # so'zma-so'z: so'rov so'zi nomning biror so'ziga yaqin bo'lsa
    words = name.split()
    for w in q.split():
        if len(w) < 3:
            continue
        for nw in words:
            r = difflib.SequenceMatcher(None, w, nw[: max(len(w) + 2, 3)]).ratio()
            best = max(best, r * 0.95)
    return best


def search_products(query: str, names: Sequence[str], limit: int = 3,
                    cutoff: float = 0.55) -> List[str]:
    """Eng yaqin `limit` ta nom (kamayish tartibida). Mos kelmasa bo'sh ro'yxat."""
    q = normalize(query)
    if not q:
        return []
    normed = {n: normalize(n) for n in names}
    # difflib.get_close_matches bilan ham tekshiriladi (klassik "balki shuni nazarda tutgansiz")
    close = set(difflib.get_close_matches(_compact(q), [_compact(v) for v in normed.values()],
                                          n=limit * 2, cutoff=cutoff))
    scored = []
    for name, nn in normed.items():
        sc = _score(q, nn)
        if _compact(nn) in close:
            sc = max(sc, cutoff + 0.01)
        if sc >= cutoff:
            scored.append((sc, name))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return [n for _, n in scored[:limit]]


def exact_match(query: str, names: Iterable[str]) -> str | None:
    """Yozuvdan qat'i nazar aynan bir xil nom (masalan "петля" == "Petlya")."""
    q = normalize(query)
    for n in names:
        if normalize(n) == q:
            return n
    return None
