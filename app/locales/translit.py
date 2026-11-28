"""O'zbek lotin -> Ўзбек кирилл (standart imlo qoidalari) va rus kirill -> lotin transliteratsiyasi.

`latin_to_cyrillic` — bot matnlari uchun: HTML teglari, {o'rinbosarlar}, &entities;, /buyruqlar, URL lar, rus
(kirill) matn, raqamli kodlar (P220, 1/19, KN95) va brendlar (Google, Telegram, PDF ...) o'zgarmaydi.
`russian_to_latin` — mahsulot nomlari uchun zaxira (lug'atda bo'lmagan so'zlar)."""
from __future__ import annotations

import re

_APOS = "'‘’ʻ`ʼ"
_KEEP = {"google", "sheets", "telegram", "pdf", "csv", "excel", "akfix", "gemini", "ai", "id", "html", "mini", "app",
         "ok", "pva", "pvx", "uzs", "gb", "mb", "url", "api", "admin_ids"}
_PROTECT = re.compile(r"(<[^>]+>|\{[^{}]*\}|&[a-zA-Z#0-9]+;|https?://\S+|(?<![\w/])/[a-z_]+|@\w+)")
_WORD = re.compile(r"[A-Za-z][A-Za-z'‘’ʻ`ʼ]*")

_DIGRAPHS = (("o'", "ў"), ("g'", "ғ"), ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"))
_SINGLE = {"a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к",
           "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у",
           "v": "в", "x": "х", "y": "й", "z": "з", "c": "с", "w": "в"}
_VOWELS = set("aeiou")


def _word_to_cyr(word: str) -> str:
    low = word.lower()
    if low in _KEEP or (len(word) >= 2 and word.isupper() and len(word) <= 4):
        return word                                       # brend / qisqartma (PDF, CSV, AI)
    w = word
    for a in _APOS:
        w = w.replace(a, "'")
    out = []
    i = 0
    lw = w.lower()
    while i < len(w):
        ch, lch = w[i], lw[i]
        upper = ch.isupper()
        pair = lw[i:i + 2]
        piece = None
        for lat, cyr in _DIGRAPHS:
            if pair == lat:
                piece, i = cyr, i + 2
                break
        if piece is None and lw[i:i + 4] == "tsiy":
            piece, i = "ц", i + 2                         # -tsiya -> -ция (Инвентаризация)
        if piece is None:
            if lch == "'":
                prev_letter = i > 0 and lw[i - 1].isalpha()
                next_letter = i + 1 < len(lw) and lw[i + 1].isalpha()
                piece = "ъ" if prev_letter and next_letter else "'"
                i += 1
            elif lch == "y" and i + 1 < len(lw) and lw[i + 1] == "e":
                piece, i = "е", i + 2                     # ye -> е
            elif lch == "e" and (i == 0 or lw[i - 1] in _VOWELS):
                piece, i = "э", i + 1                     # so'z boshida / unlidan keyin: э
            else:
                piece, i = _SINGLE.get(lch, ch), i + 1
        if upper and piece:
            # «Sh» -> «Ш», «SH» -> «Ш»
            piece = piece[0].upper() + piece[1:]
        out.append(piece)
    return "".join(out)


def _convert_plain(text: str) -> str:
    return _WORD.sub(lambda m: _word_to_cyr(m.group(0)) if not re.search(r"\d", m.group(0)) else m.group(0), text)


def latin_to_cyrillic(text: str) -> str:
    """O'zbek lotin matnini kirillga o'giradi (himoyalangan qismlarga tegmaydi)."""
    if not text:
        return text
    parts = _PROTECT.split(str(text))
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:                                    # himoyalangan bo'lak
            out.append(part)
        else:
            # raqam + harf aralash kodlar (P220, KN95, 35mm) o'zgarmaydi
            out.append(re.sub(r"[A-Za-z'‘’ʻ`ʼ]*\d[\w/]*|[A-Za-z][A-Za-z'‘’ʻ`ʼ]*",
                              lambda m: m.group(0) if re.search(r"\d", m.group(0)) else _word_to_cyr(m.group(0)),
                              part))
    return "".join(out)


_RU_LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
           "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
           "у": "u", "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "",
           "э": "e", "ю": "yu", "я": "ya", "ў": "o'", "ғ": "g'", "қ": "q", "ҳ": "h"}


def russian_to_latin(text: str) -> str:
    out = []
    s = str(text or "")
    for i, ch in enumerate(s):
        low = ch.lower()
        if low in _RU_LAT:
            lat = _RU_LAT[low]
            if low == "е" and (i == 0 or not s[i - 1].isalpha()):
                lat = "ye"                                # so'z boshida: ye
            if ch.isupper() and lat:
                lat = lat[0].upper() + lat[1:]
            out.append(lat)
        else:
            out.append(ch)
    return "".join(out)
