"""Kategoriyalar: standart zavod kategoriyalari, ikki tilli ko'rinish va aqlli avto-kategoriyalash.

Jadvalga (Google Sheets "Категория" ustuni) DOIM kanonik nom yoziladi (standartlar uchun ruscha), shunda
filtrlar va SUMIFS formulalari tilga bog'liq bo'lmaydi. Botda esa foydalanuvchi tilida ko'rsatiladi."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.search import normalize


@dataclass(frozen=True)
class DefaultCategory:
    name: str                 # jadvalga yoziladigan kanonik nom
    uz: str
    ru: str
    examples_uz: str
    examples_ru: str
    keywords: Tuple[str, ...]  # lotin / kirill ildizlar (normalize() orqali solishtiriladi)


DEFAULT_CATEGORIES: Tuple[DefaultCategory, ...] = (
    DefaultCategory("Фурнитура", "🔩 Furnitura", "🔩 Фурнитура",
                    "petlya, ruchka, salazka, zamok", "петли, ручки, салазки, замки",
                    ("petlya", "petlia", "petla", "ruchka", "salazka", "salaska", "napravlyayush", "zamok",
                     "opora", "nojka", "kryuchok", "kronshteyn", "podyomnik", "gazlift", "доводчик", "petl",
                     "петл", "ручк", "салазк", "направляющ", "замок", "опор", "ножк", "крючок", "кронштейн",
                     "газлифт", "подъемник",
                     # mebel furniturasi: tiqinlar, oyoqchalar, mexanizmlar, burchaklar, profillar
                     "механизм", "заглушк", "пробк", "каблук", "подпятник", "полкодержател", "уголок", "угольник",
                     "профил", "рельс", "купе", "шпингалет", "вешалк", "карниз", "кольц", "планк", "плинтус")),
    DefaultCategory("Крепеж / Метизы", "🪛 Mix va Samorezlar", "🪛 Крепеж / Метизы",
                    "samorez, mix, bolt, gayka, yevrostyajka", "саморезы, гвозди, болты, гайки, евровинты",
                    ("samorez", "mix", "gvozd", "bolt", "gayka", "shayba", "vint", "shurup", "dyubel",
                     "yevrostyajka", "evrostyajka", "evrovint", "konfirmat", "shpilka", "skoba", "саморез",
                     "гвозд", "болт", "гайк", "шайб", "винт", "шуруп", "дюбел", "евростяжк", "евровинт",
                     "конфирмат", "шпильк", "скоб", "метиз", "креп", "заклепк", "хомут", "резьб", "соединител", "анкер")),
    DefaultCategory("Клей и Химия", "🧪 Kley va Ximiya", "🧪 Клей и Химия",
                    "kley, PVA, kromka, germetik, lak", "клей, ПВА, кромка, герметик, лак",
                    ("kley", "pva", "akfix", "akfiks", "kromka", "germetik", "lak", "kraska", "rastvoritel",
                     "pena", "silikon", "morilka", "клей", "пва", "акфикс", "кромк", "герметик", "лак",
                     "краск", "растворител", "пена", "силикон", "морилк", "химия", "разбавител", "шпатлевк", "эмульси", "затирк", "фуга", "масло", "грунт",
                     "растворит", "ральф")),
    DefaultCategory("Поролон и Обивка", "🛋 Porolon va Qoplama", "🛋 Поролон и Обивка",
                    "porolon, mato, lipuchka, sintepon", "поролон, ткань, липучка, синтепон",
                    ("porolon", "lipuchka", "mato", "tkan", "sintepon", "velur", "kojzam", "ekokoja", "flok",
                     "obivk", "spanbond", "paralon", "поролон", "паралон", "липучк", "ткан", "синтепон", "велюр", "кожзам",
                     "экокож", "флок", "обивк", "спанбонд", "матрас", "обои", "абой")),
    DefaultCategory("Расходники", "📦 Sarf materiallari", "📦 Расходники",
                    "shkurka, perchatka, skotch, meshok, plyonka", "шкурка, перчатки, скотч, мешки, плёнка",
                    ("shkurka", "nazhdak", "perchatka", "skotch", "meshok", "plyonka", "plenka", "streych",
                     "kist", "valik", "lenta", "шкурк", "наждак", "перчатк", "скотч", "мешок", "мешк",
                     "плёнк", "пленк", "стрейч", "кист", "валик", "лент", "упаковк", "маск", "веник", "веревк", "сварочн", "баннер")),
    DefaultCategory("Инструменты", "🛠 Asbob-uskunalar", "🛠 Инструменты",
                    "ruletka, bita, sverlo, disk, pila", "рулетка, биты, свёрла, диски, пилы",
                    ("ruletka", "bita", "sverlo", "disk", "pila", "frez", "otvertka", "stepler", "molotok",
                     "nojovka", "shurupovert", "shrupovert", "рулетк", "бит", "сверл", "свёрл", "диск", "пил", "фрез",
                     "отвертк", "отвёртк", "степлер", "молоток", "ножовк", "шуруповерт", "шруповерт", "инструмент", "перфоратор", "пистолет", "генератор",
                     "болгарк", "дрел")),
)

_BY_NAME: Dict[str, DefaultCategory] = {c.name.casefold(): c for c in DEFAULT_CATEGORIES}
_KEYWORDS: List[Tuple[str, str]] = sorted(
    {(normalize(k).replace(" ", ""), c.name) for c in DEFAULT_CATEGORIES for k in c.keywords},
    key=lambda x: -len(x[0]))


def default_of(name: str) -> Optional[DefaultCategory]:
    return _BY_NAME.get(str(name).strip().casefold())


def label(name: str, lang: str) -> str:
    """Botda ko'rinadigan nom: standart kategoriya — foydalanuvchi tilida, boshqasi — aynan saqlangan nom."""
    d = default_of(name)
    if d is None:
        return name
    return d.ru if lang == "ru" else d.uz


def examples(names: Sequence[str], lang: str) -> str:
    """Kategoriya tugmalari ustidagi qisqa yo'riqnoma: har bir standart kategoriyaga misollar."""
    lines = []
    for n in names:
        d = default_of(n)
        if d is not None:
            lines.append(f"{label(n, lang)} — {d.examples_ru if lang == 'ru' else d.examples_uz}")
    return "\n".join(lines)


def merge(stored: Iterable[str], from_sheet: Iterable[str]) -> List[str]:
    """Saqlangan ro'yxat (tartibi bilan) + jadvalda uchragan, lekin ro'yxatda yo'q kategoriyalar."""
    out: List[str] = []
    seen = set()
    for n in list(stored) + sorted(from_sheet, key=str.casefold):
        n = str(n).strip()
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    return out


def _words(name: str) -> List[str]:
    return [w for w in re.split(r"[\s\-_/.,()]+", normalize(name)) if len(w) >= 4 and not w.isdigit()]


def suggest(name: str, products: Iterable[Tuple[str, str]]) -> Optional[str]:
    """Yangi mahsulot nomiga mos kategoriya.

    1) Katalogdagi o'xshash mahsulotlar: BIRINCHI so'zining ildizi (5 harf) bir xil bo'lganlari — eng ko'p
       uchragan kategoriyasi. Faqat birinchi so'z: "Сверло для саморезов" — bu sverlo, saморез emas;
    2) ikki tilli (kirill/lotin) kalit so'zlar lug'ati.
    `products`: (mahsulot nomi, kategoriya) juftlari."""
    words = _words(name)
    if words:
        root = words[0][:5]
        votes: Counter = Counter()
        for pname, cat in products:
            other = _words(pname)
            if cat and other and other[0][:5] == root:
                votes[cat.strip()] += 1
        if votes:
            return votes.most_common(1)[0][0]
    # nomda ENG OLDIN uchragan kalit so'z yutadi (birinchi so'z odatda tovarning o'zi: "Ручка-скоба" — ручка),
    # teng bo'lsa — uzunrog'i
    flat = normalize(name).replace(" ", "")
    best: Optional[Tuple[int, int, str]] = None
    for kw, cat in _KEYWORDS:
        pos = flat.find(kw) if kw else -1
        if pos >= 0 and (best is None or (pos, -len(kw)) < (best[0], best[1])):
            best = (pos, -len(kw), cat)
    return best[2] if best else None
