"""Mahsulot nomi, o'lchov birligi va kategoriyani foydalanuvchi tilida ko'rsatish: `uz` (lotin), `cyrl` (кирилл),
`ru` (рус). Google Sheets dagi KANONIK nomlarga tegilmaydi (SUMIFS / formulalar buzilmaydi) — faqat ko'rinish.

- ru   — jadvaldagi nom aynan;
- uz   — mebel atamalari lug'ati (iboralar, so'zlar) + qolgan so'zlar rus -> lotin transliteratsiya;
- cyrl — o'zbek lotin natijasidan kirillga.
Qidiruv: foydalanuvchi uch yozuvning istalganida yozsa ham (`Ilgak`, `Илгак`, `Крючок`) kanonik nomga qaytadi."""
from __future__ import annotations

import re
from functools import lru_cache
from html import escape
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.locales.translit import latin_to_cyrillic, russian_to_latin

# iboralar (avval), keyin alohida so'zlar. Kalit — kichik harf, rus.
_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("кромка пвх", "kromka PVX"), ("клей для кромки", "kromka kleyi"), ("для кромки", "kromka uchun"),
    ("пена монтажная", "montaj ko'pigi"), ("маска защитная", "himoya niqobi"), ("перчатки рабочие", "ish qo'lqoplari"),
    ("гвозди строительные", "qurilish mixi"), ("пистолет продувочный воздушный", "havo puflash pistoleti"),
    ("заглушка кабель-канала", "kabel-kanal qopqog'i"), ("труба хромированная", "xromlangan quvur"),
    ("с тормозом", "tormozli"), ("без тормоза", "tormozsiz"), ("герметик силиконовый", "silikon germetik"),
    ("шкурка сеточная", "to'rli jilvir"), ("шкурка круглая", "dumaloq jilvir"), ("скотч двусторонний", "ikki tomonlama skotch"),
    ("скотч малярный", "bo'yoqchi skotchi"), ("скотч прозрачный", "shaffof skotch"), ("уголок мебельный", "mebel burchagi"),
    ("уголок металлический", "metall burchak"), ("замок мебельный", "mebel qulfi"), ("замок центральный", "markaziy qulf"),
    ("механизм купе", "kupe mexanizmi"), ("механизм барный", "bar mexanizmi"), ("ножка мебельная", "mebel oyog'i"),
    ("ролик мебельный", "mebel g'ildiragi"), ("соединитель мебельный", "mebel biriktirgichi"),
    ("подпятник мебельный", "mebel tagligi"), ("шпингалет мебельный", "mebel shpingaleti"),
    ("крючок мебельный", "mebel ilgagi"), ("петля вкладная", "ichki petlya"), ("петля полунакладная", "yarim ustki petlya"),
    ("петля накладная", "ustki petlya"),
)
_WORDS: Dict[str, str] = {
    "ручка": "ruchka", "салазки": "salazka", "саморез": "samorez", "скоба": "skoba", "скотч": "skotch",
    "клей": "kley", "пва": "PVA", "гранулы": "granula", "герметик": "germetik", "силиконовый": "silikon",
    "маска": "niqob", "перчатки": "qo'lqop", "шкурка": "jilvir", "сеточная": "to'rli", "круглая": "dumaloq",
    "кромка": "kromka", "пвх": "PVX", "соединитель": "biriktirgich", "евростяжка": "yevrostyajka",
    "конфирмат": "konfirmat", "механизм": "mexanizm", "комплект": "komplekt", "трубодержатель": "quvur ushlagich",
    "фланец": "flanes", "труба": "quvur", "луза": "luza", "замок": "qulf", "длинный": "uzun", "короткий": "qisqa",
    "центральный": "markaziy", "уголок": "burchak", "пластиковый": "plastik", "металлический": "metall",
    "гвозди": "mix", "ножка": "oyoq", "бинго": "Bingo", "ролик": "g'ildirak", "петля": "petlya",
    "горбатая": "bukri", "полугорбатая": "yarim bukri", "прямая": "to'g'ri", "подпятник": "taglik",
    "полкодержатель": "polka ushlagich", "шпингалет": "shpingalet", "крючок": "ilgak", "черная": "qora",
    "черный": "qora", "белый": "oq", "белая": "oq", "мебельный": "mebel", "мебельная": "mebel", "мм": "mm",
    "см": "sm", "гр": "gr", "пятак": "Pyatak", "акфикс": "Akfiks", "титан": "Titan", "прозрачный": "shaffof",
    "малярный": "bo'yoqchi", "двусторонний": "ikki tomonlama", "защитная": "himoya", "рабочие": "ish",
    "строительные": "qurilish", "хромированная": "xromlangan", "винт": "vint", "болт": "bolt", "гайка": "gayka",
    "сверло": "parma", "диск": "disk", "бита": "bita", "фреза": "freza", "краска": "bo'yoq", "лак": "lak",
    "поролон": "porolon", "паралон": "porolon", "ткань": "mato", "липучка": "lipuchka", "веник": "supurgi",
    "мешок": "qop", "плёнка": "plyonka", "пленка": "plyonka", "лента": "lenta", "кисть": "mo'yqalam",
}
_UNITS: Dict[str, Tuple[str, str]] = {          # ru -> (uz, cyrl)
    "шт": ("dona", "дона"), "кг": ("kg", "кг"), "рулон": ("rulon", "рулон"), "компл": ("komplekt", "комплект"),
    "комплект": ("komplekt", "комплект"), "пачка": ("pachka", "пачка"), "пара": ("juft", "жуфт"),
    "каробка": ("quti", "қути"), "коробка": ("quti", "қути"), "упак": ("qadoq", "қадоқ"), "м": ("metr", "метр"),
    "литр": ("litr", "литр"), "л": ("litr", "литр"), "лист": ("varaq", "варақ"), "кв. м": ("kv. m", "кв. м"),
    "баллон": ("ballon", "баллон"), "банка": ("banka", "банка"), "мешок": ("qop", "қоп"), "ведро": ("chelak", "челак"),
}
_CYR = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")


def _cap(word: str, like: str) -> str:
    return word[:1].upper() + word[1:] if like[:1].isupper() and word else word


@lru_cache(maxsize=4096)
def _uz_name(name: str) -> str:
    s = str(name)
    if not _CYR.search(s):
        return s
    low = s.lower()
    # iboralar: o'rnini saqlab almashtiriladi
    for ru, uz in _PHRASES:
        idx = low.find(ru)
        while idx >= 0:
            repl = _cap(uz, s[idx:idx + 1])
            s = s[:idx] + repl + s[idx + len(ru):]
            low = s.lower()
            idx = low.find(ru, idx + len(repl))

    def word(m: "re.Match[str]") -> str:
        w = m.group(0)
        hit = _WORDS.get(w.lower())
        return _cap(hit, w) if hit is not None else russian_to_latin(w)
    return re.sub(r"[А-Яа-яЁёЎўҚқҒғҲҳ]+", word, s)


def localize_product_name(name: str, lang: str) -> str:
    if lang == "ru" or not name:
        return name
    uz = _uz_name(name)
    return latin_to_cyrillic(uz) if lang == "cyrl" else uz


def localize_unit(unit: str, lang: str) -> str:
    u = str(unit or "").strip()
    if lang == "ru" or not u:
        return u
    hit = _UNITS.get(u.lower().rstrip("."))
    if hit:
        return hit[1] if lang == "cyrl" else hit[0]
    uz = russian_to_latin(u) if _CYR.search(u) else u
    return latin_to_cyrillic(uz) if lang == "cyrl" else uz


def localize_category(cat: str, lang: str) -> str:
    from app.services.categories import label
    return label(cat, lang)


# ---- HTML uchun qisqa yordamchilar (t — get_texts natijasi) ----
def _lang(t: Dict[str, str]) -> str:
    return t.get("lang_code", "uz") if isinstance(t, dict) else str(t)


def pn(t, name: str) -> str:
    """Mahsulot nomi (HTML-escape bilan) foydalanuvchi tilida."""
    return escape(localize_product_name(name, _lang(t)), quote=False)


def pu(t, unit: str) -> str:
    return escape(localize_unit(unit, _lang(t)), quote=False)


def names(t, items: Sequence[str]) -> List[str]:
    lang = _lang(t)
    return [localize_product_name(n, lang) for n in items]


# ---- uch yozuvda qidiruv ----
def _norm(text: str) -> str:
    from app.services.search import normalize
    return normalize(text).replace(" ", "")


def aliases(name: str) -> List[str]:
    """Kanonik nom + o'zbek lotin + kirill ko'rinishi (normalize qilingan)."""
    out = {_norm(name), _norm(_uz_name(name)), _norm(latin_to_cyrillic(_uz_name(name)))}
    return [a for a in out if a]


def exact_any(query: str, catalog_names: Iterable[str]) -> Optional[str]:
    q = _norm(query)
    if not q:
        return None
    for n in catalog_names:
        if q in aliases(n):
            return n
    return None


def contains_any(query: str, catalog_names: Iterable[str]) -> List[str]:
    q = _norm(query)
    if not q:
        return []
    return [n for n in catalog_names if any(q in a for a in aliases(n))]


def fuzzy_any(query: str, catalog_names: Sequence[str], limit: int = 3) -> List[str]:
    """O'xshash nomlar: kanonik va mahalliy ko'rinishlar bo'yicha (kanonik nom qaytadi)."""
    from app.services.search import search_products
    by_alias: Dict[str, str] = {}
    for n in catalog_names:
        by_alias.setdefault(n, n)
        by_alias.setdefault(_uz_name(n), n)
    hits = search_products(query, list(by_alias), limit=limit * 2)
    out: List[str] = []
    for h in hits:
        c = by_alias[h]
        if c not in out:
            out.append(c)
    return out[:limit]
