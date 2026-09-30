"""Детерминированная нормализация поисковых полей профиля.

Используется в сервисном слое (intake, edit) и в web-роутах.
Не зависит ни от каких внешних сервисов — только статические словари.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from functools import lru_cache

from app.services.geo_distance import canonical_city, is_known_place, place_weight

# Названия городов на русском, украинском и английском → официальное немецкое
# написание. Словарь покрывает крупные города и населённые пункты Мекленбурга;
# всё, чего в нём нет, разбирает транслитерация ниже по тексту.
CITY_TO_DE: dict[str, str] = {
    "берлин": "Berlin", "берлін": "Berlin",
    "гамбург": "Hamburg",
    "мюнхен": "München", "munich": "München", "munchen": "München", "muenchen": "München",
    "кёльн": "Köln", "кельн": "Köln", "cologne": "Köln", "koln": "Köln", "koeln": "Köln",
    "франкфурт": "Frankfurt am Main", "франкфурт-на-майне": "Frankfurt am Main", "франкфурт на майне": "Frankfurt am Main", "франкфурт-на-майні": "Frankfurt am Main", "франкфурт на майні": "Frankfurt am Main", "frankfurt": "Frankfurt am Main",
    "штутгарт": "Stuttgart",
    "дюссельдорф": "Düsseldorf", "dusseldorf": "Düsseldorf", "duesseldorf": "Düsseldorf",
    "лейпциг": "Leipzig",
    "дортмунд": "Dortmund",
    "эссен": "Essen", "ессен": "Essen",
    "бремен": "Bremen",
    "дрезден": "Dresden",
    "ганновер": "Hannover", "hanover": "Hannover",
    "нюрнберг": "Nürnberg", "нюрнберґ": "Nürnberg", "nuremberg": "Nürnberg", "nurnberg": "Nürnberg", "nuernberg": "Nürnberg",
    "дуйсбург": "Duisburg",
    "бохум": "Bochum",
    "вупперталь": "Wuppertal",
    "билефельд": "Bielefeld", "білефельд": "Bielefeld",
    "бонн": "Bonn",
    "мюнстер": "Münster", "munster": "Münster", "muenster": "Münster",
    "мангейм": "Mannheim", "маннгейм": "Mannheim", "мангайм": "Mannheim",
    "карлсруэ": "Karlsruhe", "карлсруе": "Karlsruhe",
    "аугсбург": "Augsburg",
    "висбаден": "Wiesbaden", "вісбаден": "Wiesbaden",
    "мёнхенгладбах": "Mönchengladbach", "менхенгладбах": "Mönchengladbach", "monchengladbach": "Mönchengladbach",
    "гельзенкирхен": "Gelsenkirchen", "гельзенкірхен": "Gelsenkirchen",
    "брауншвейг": "Braunschweig", "brunswick": "Braunschweig",
    "киль": "Kiel", "кіль": "Kiel",
    "ахен": "Aachen", "аахен": "Aachen",
    "хемниц": "Chemnitz", "хемніц": "Chemnitz",
    "галле": "Halle", "halle saale": "Halle",
    "магдебург": "Magdeburg",
    "фрайбург": "Freiburg im Breisgau", "фрейбург": "Freiburg im Breisgau", "freiburg": "Freiburg im Breisgau",
    "крефельд": "Krefeld",
    "майнц": "Mainz",
    "любек": "Lübeck", "lubeck": "Lübeck", "luebeck": "Lübeck",
    "эрфурт": "Erfurt", "ерфурт": "Erfurt",
    "оберхаузен": "Oberhausen",
    "росток": "Rostock",
    "кассель": "Kassel",
    "хаген": "Hagen", "гаген": "Hagen",
    "потсдам": "Potsdam",
    "саарбрюккен": "Saarbrücken", "саарбрюкен": "Saarbrücken", "saarbrucken": "Saarbrücken", "saarbruecken": "Saarbrücken",
    "хамм": "Hamm",
    "людвигсхафен": "Ludwigshafen am Rhein", "людвігсхафен": "Ludwigshafen am Rhein", "ludwigshafen": "Ludwigshafen am Rhein",
    "мюльхайм": "Mülheim an der Ruhr", "мюльгейм": "Mülheim an der Ruhr", "мюльгайм": "Mülheim an der Ruhr", "mulheim": "Mülheim an der Ruhr",
    "ольденбург": "Oldenburg",
    "оснабрюк": "Osnabrück", "osnabruck": "Osnabrück", "osnabrueck": "Osnabrück",
    "леверкузен": "Leverkusen",
    "гейдельберг": "Heidelberg", "хайдельберг": "Heidelberg", "гайдельберг": "Heidelberg",
    "дармштадт": "Darmstadt",
    "золинген": "Solingen", "золінген": "Solingen",
    "регенсбург": "Regensburg",
    "херне": "Herne", "герне": "Herne",
    "падерборн": "Paderborn",
    "нойс": "Neuss",
    "ингольштадт": "Ingolstadt", "інгольштадт": "Ingolstadt",
    "оффенбах": "Offenbach am Main", "офенбах": "Offenbach am Main", "offenbach": "Offenbach am Main",
    "фюрт": "Fürth", "furth": "Fürth", "fuerth": "Fürth",
    "вюрцбург": "Würzburg", "wurzburg": "Würzburg", "wuerzburg": "Würzburg",
    "ульм": "Ulm",
    "хайльбронн": "Heilbronn", "гейльбронн": "Heilbronn", "гайльбронн": "Heilbronn",
    "пфорцхайм": "Pforzheim", "пфорцгейм": "Pforzheim", "пфорцгайм": "Pforzheim",
    "вольфсбург": "Wolfsburg",
    "гёттинген": "Göttingen", "геттинген": "Göttingen", "геттінген": "Göttingen", "gottingen": "Göttingen", "goettingen": "Göttingen",
    "боттроп": "Bottrop", "ботроп": "Bottrop",
    "ройтлинген": "Reutlingen", "ройтлінген": "Reutlingen",
    "кобленц": "Koblenz",
    "бремерхафен": "Bremerhaven", "бремергафен": "Bremerhaven",
    "бергиш-гладбах": "Bergisch Gladbach", "бергиш гладбах": "Bergisch Gladbach", "бергіш-гладбах": "Bergisch Gladbach",
    "реклингхаузен": "Recklinghausen", "реклінгхаузен": "Recklinghausen",
    "йена": "Jena", "иена": "Jena", "єна": "Jena",
    "ремшайд": "Remscheid", "ремшейд": "Remscheid",
    "эрланген": "Erlangen", "ерланген": "Erlangen",
    "трир": "Trier", "трір": "Trier",
    "зальцгиттер": "Salzgitter", "зальцгіттер": "Salzgitter",
    "зиген": "Siegen", "зіген": "Siegen",
    "мёрс": "Moers", "мерс": "Moers",
    "котбус": "Cottbus", "коттбус": "Cottbus",
    "хильдесхайм": "Hildesheim", "гильдесгейм": "Hildesheim", "гільдесгайм": "Hildesheim",
    "гютерсло": "Gütersloh", "gutersloh": "Gütersloh", "guetersloh": "Gütersloh",
    "кайзерслаутерн": "Kaiserslautern",
    "шверин": "Schwerin", "шверін": "Schwerin",
    "штральзунд": "Stralsund",
    "грайфсвальд": "Greifswald", "грейфсвальд": "Greifswald",
    "нойбранденбург": "Neubrandenburg",
    "висмар": "Wismar", "вісмар": "Wismar",
    "гюстров": "Güstrow", "gustrow": "Güstrow", "guestrow": "Güstrow",
    "фленсбург": "Flensburg",
    "констанц": "Konstanz",
    "цвиккау": "Zwickau", "цвіккау": "Zwickau",
    "гера": "Gera",
    "байройт": "Bayreuth",
    "бамберг": "Bamberg",
    "пассау": "Passau",
    "розенхайм": "Rosenheim", "розенгейм": "Rosenheim", "розенгайм": "Rosenheim",
    "ландсхут": "Landshut", "ландсгут": "Landshut",
    "люнебург": "Lüneburg", "luneburg": "Lüneburg", "lueneburg": "Lüneburg",
    "целле": "Celle",
    "марбург": "Marburg",
    "гисен": "Gießen", "гиссен": "Gießen", "гісен": "Gießen", "giessen": "Gießen",
    "фульда": "Fulda",
    "веймар": "Weimar",
    "дессау": "Dessau-Roßlau", "dessau": "Dessau-Roßlau",
    "плауэн": "Plauen", "плауен": "Plauen",
    "гёрлиц": "Görlitz", "герлиц": "Görlitz", "герліц": "Görlitz", "gorlitz": "Görlitz", "goerlitz": "Görlitz",
    "айзенах": "Eisenach", "эйзенах": "Eisenach",
    "нордхаузен": "Nordhausen", "нордгаузен": "Nordhausen",
    "дюрен": "Düren", "duren": "Düren", "dueren": "Düren",
    "эмден": "Emden", "емден": "Emden",
    "ханау": "Hanau", "ганау": "Hanau",
    "виттен": "Witten", "віттен": "Witten",
    "изерлон": "Iserlohn", "ізерлон": "Iserlohn",
    "ратинген": "Ratingen", "ратінген": "Ratingen",
    "люнен": "Lünen", "lunen": "Lünen", "luenen": "Lünen",
    "фельберт": "Velbert",
    "минден": "Minden", "мінден": "Minden",
    "вормс": "Worms",
    "ноймюнстер": "Neumünster", "neumunster": "Neumünster", "neumuenster": "Neumünster",
    "нордерштедт": "Norderstedt",
    "дельменхорст": "Delmenhorst", "дельменгорст": "Delmenhorst",
    "вильгельмсхафен": "Wilhelmshaven", "вільгельмсгафен": "Wilhelmshaven",
    "бад-доберан": "Bad Doberan", "бад доберан": "Bad Doberan",
    "рибниц-дамгартен": "Ribnitz-Damgarten", "рибниц дамгартен": "Ribnitz-Damgarten", "рібніц-дамгартен": "Ribnitz-Damgarten",
    "варен": "Waren",
    "нойштрелиц": "Neustrelitz", "нойштреліц": "Neustrelitz",
    "пархим": "Parchim", "пархім": "Parchim",
    "людвигслуст": "Ludwigslust", "людвігслуст": "Ludwigslust",
    "анклам": "Anklam",
    "деммин": "Demmin", "демін": "Demmin",
    "вольгаст": "Wolgast",
    "пазевальк": "Pasewalk",
    "бютцов": "Bützow", "butzow": "Bützow", "buetzow": "Bützow",
    "тетеров": "Teterow", "тетерів": "Teterow",
    "засниц": "Sassnitz", "засніц": "Sassnitz",
    "бинц": "Binz", "бінц": "Binz",
    "барт": "Barth",
    "лааге": "Laage",
    "тессин": "Tessin", "тессін": "Tessin",
    "думмерсторф": "Dummerstorf", "думерсторф": "Dummerstorf",
    "кюлунгсборн": "Kühlungsborn", "kuhlungsborn": "Kühlungsborn", "kuehlungsborn": "Kühlungsborn",
    "вся германия": "Deutschland", "по всей германии": "Deutschland", "германия": "Deutschland", "по германии": "Deutschland", "вся німеччина": "Deutschland", "німеччина": "Deutschland", "deutschland": "Deutschland", "germany": "Deutschland",
}


# Русские роли → первичный немецкий keyword для job-API
ROLE_TO_DE_QUERY: dict[str, str] = {
    "склад": "lager",
    "логистика": "logistik",
    "упаковка": "verpacker",
    "производство": "produktion",
    "комплектовщик": "kommissionierer",
    "грузчик": "lagerhelfer",
    "рабочий помощник": "helfer",
    "курьер": "kurier",
    "водитель": "fahrer",
    "уборщик": "reinigungskraft",
    "повар": "koch",
    "официант": "kellner",
    "охранник": "sicherheitsdienst",
    "строитель": "bauhelfer",
    "it": "softwareentwickler",
    "программист": "softwareentwickler",
    "монтажник": "monteur",
    "электрик": "elektriker",
    "сварщик": "schweißer",
    "слесарь": "schlosser",
    "оператор": "maschinenführer",
    "кладовщик": "lagerist",
    "экспедитор": "disponent",
    "медсестра": "pflegekraft",
    "бухгалтер": "buchhalter",
    "менеджер": "manager",
    "продавец": "verkäufer",
    "кассир": "kassierer",
    # Rarer roles (kept in sync with ROLE_INTENT_MAP in _role_intent_lexicon.py).
    "таксист": "taxifahrer",
    "дальнобойщик": "berufskraftfahrer",
    "бармен": "barkeeper",
    "бариста": "barista",
    "пекарь": "bäcker",
    "кондитер": "konditor",
    "мясник": "metzger",
    "врач": "arzt",
    "физиотерапевт": "physiotherapeut",
    "стоматолог": "zahnarzt",
    "маляр": "maler",
    "плиточник": "fliesenleger",
    "штукатур": "stuckateur",
    "плотник": "zimmermann",
    "столяр": "tischler",
    "каменщик": "maurer",
    "кровельщик": "dachdecker",
    "сантехник": "anlagenmechaniker",
    "автомеханик": "kfz-mechaniker",
    "швея": "näherin",
    "разнорабочий": "helfer",
    "сторож": "sicherheitsdienst",
    # Автомобильная логистика. Здесь, в отличие от остальных записей, немецкое
    # слово составное: normalize_query_from_roles берёт только первое слово, и
    # для перегона это именно то слово, которое понимают немецкие job-API.
    "перегон автомобилей": "Fahrzeugüberführer",
    "перегон авто": "Fahrzeugüberführer",
    "перегонщик": "Fahrzeugüberführer",
    "перегонщик автомобилей": "Fahrzeugüberführer",
    "автомобильная логистика": "Fahrzeuglogistik",
    "автологистика": "Fahrzeuglogistik",
    "подготовка автомобилей": "Fahrzeugaufbereiter",
}


# Кириллица → латиница в том виде, в каком её обычно читают по-немецки.
# Точного соответствия не бывает: "Гамбург" даёт "gamburg", а не "hamburg",
# поэтому результат не принимается на веру, а проверяется по справочнику
# населённых пунктов — см. _transliterated_city.
_CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "w", "г": "g", "д": "d", "е": "e", "ё": "o", "ж": "sch",
    "з": "s", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "z",
    "ч": "tsch", "ш": "sch", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "u", "я": "a", "і": "i", "ї": "i", "є": "e", "ґ": "g", "'": "", "\u2019": "",
}

# Расхождения между русским прочтением и немецким письмом. Каждое правило —
# это пара «как слышится» / «как пишется»: "Гамбург" → hamburg, "Росток" →
# rostock, "Хемниц" → chemnitz. Варианты перебираются и проверяются по
# справочнику, поэтому лишнее правило не портит результат — оно лишь добавляет
# написание, которого в Германии нет.
_TRANSLITERATION_ALTERNATIVES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sch", ("sch", "s")),
    ("ai", ("ai", "ei")),
    ("oi", ("oi", "eu")),
    ("kw", ("kw", "qu")),
    ("h", ("h", "ch")),
    ("g", ("g", "h")),
    ("w", ("w", "v")),
    ("z", ("z", "tz", "c")),
    ("i", ("i", "ie")),
    ("k", ("k", "ck")),
    ("s", ("s", "ss")),
    ("t", ("t", "th")),
    ("u", ("u", "uh")),
)
_MAX_TRANSLITERATION_VARIANTS = 4096
_CYRILLIC_RE = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)


def _lexicon_key(location: str) -> str:
    """Ключ словаря: регистр и длина пробелов значения не имеют."""
    return " ".join(location.strip().casefold().split())


def _transliterate(text: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(char, char) for char in text.casefold())


def _transliteration_variants(word: str) -> tuple[str, ...]:
    """Написания, которые могли иметься в виду под этой транслитерацией.

    Порядок перебора фиксирован, а не отдан множеству: при переполнении
    лимита список обрезается, и от порядка зависит результат. Один и тот же
    город обязан разрешаться одинаково при каждом запуске.
    """
    variants: dict[str, None] = {word: None}
    for pattern, replacements in _TRANSLITERATION_ALTERNATIVES:
        expanded: dict[str, None] = {}
        for variant in variants:
            positions = [
                index for index in range(len(variant)) if variant.startswith(pattern, index)
            ]
            if not positions:
                expanded[variant] = None
                continue
            for choice in itertools.product(replacements, repeat=len(positions)):
                pieces: list[str] = []
                previous = 0
                for position, replacement in zip(positions, choice, strict=True):
                    pieces.append(variant[previous:position])
                    pieces.append(replacement)
                    previous = position + len(pattern)
                pieces.append(variant[previous:])
                expanded["".join(pieces)] = None
                if len(expanded) >= _MAX_TRANSLITERATION_VARIANTS:
                    break
            if len(expanded) >= _MAX_TRANSLITERATION_VARIANTS:
                break
        variants = expanded
    return tuple(variants)


@lru_cache(maxsize=512)
def _transliterated_city(location: str) -> str | None:
    """Немецкое название города, записанного кириллицей, либо None.

    Результат кэшируется: разбор поля городов повторяется несколько раз за
    запрос — в роуте, в оркестраторе, в выгрузке, — а перебор написаний для
    длинного слова стоит десятые доли секунды.

    Словарь выше знает крупные города, но человек ищет и в Гюстрове, и в
    Бютцове, и перечислить всю Германию руками нельзя. Поэтому написание
    подбирается по справочнику населённых пунктов: принимается только то, что
    в Германии действительно существует, а из нескольких подходящих берётся
    самое крупное место — у города почтовых индексов десятки, у хутора один.
    """
    if not _CYRILLIC_RE.search(location):
        return None
    transliterated = _transliterate(location)
    if not transliterated.strip():
        return None
    if is_known_place(transliterated):
        return transliterated.title()

    matches = tuple(
        variant
        for variant in _transliteration_variants(transliterated)
        if is_known_place(variant)
    )
    if not matches:
        return None
    # Из подошедших написаний берём самое крупное место: "Гера" не должна
    # уехать в одноимённую деревню, а "Целле" — в хутор Zelle.
    best = max(sorted(set(matches)), key=place_weight)
    return best.title()


def normalize_location(location: str | None) -> str | None:
    """Переводим одно название города в немецкое. Если уже немецкое — оставляем."""
    if not location:
        return None
    raw = location.strip()
    if not raw:
        return None
    mapped = CITY_TO_DE.get(_lexicon_key(raw))
    if mapped:
        return mapped
    return _transliterated_city(raw) or raw


def normalize_location_from_list(locations: list[str] | None) -> str | None:
    """Берём первую локацию из списка и нормализуем."""
    if not locations:
        return None
    return normalize_location(locations[0])


def normalize_locations(locations: Sequence[str] | None) -> tuple[str, ...]:
    """Все локации списка в немецком написании, без повторов и в том же порядке."""
    if not locations:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for location in locations:
        for city in parse_search_cities(location):
            key = canonical_city(city) or city.casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(city)
    return tuple(normalized)


def is_german_city_name(city: str | None) -> bool:
    """Удалось ли привести город к немецкому написанию.

    Оставшаяся кириллица означает, что ни словарь, ни транслитерация города не
    узнали: источники ответят на такой запрос пустотой, и честнее сказать об
    этом вслух, чем показать пустой раздел без объяснения.
    """
    if not city:
        return False
    return not _CYRILLIC_RE.search(city)


def parse_search_cities(location: str | None) -> tuple[str, ...]:
    """Города из поля поиска — по порядку, как их перечислил человек.

    Поле одно, а городов в нём может быть несколько: «Berlin, Росток». Порядок
    сохраняется, потому что он и есть приоритет — сначала показываем вакансии
    первого города, потом второго.

    Пустой результат означает «города не заданы»: удалёнка, вся страна или
    пустое поле. Название страны рядом с городами отбрасывается — «Berlin,
    Deutschland» человек пишет, уточняя страну Берлина, а не прося ещё и всю
    Германию.
    """
    if not location or is_country_wide_location(location) or is_remote_worldwide_location([location]):
        return ()

    cities: list[str] = []
    # Один и тот же город человек может написать дважды и по-разному
    # («Берлин, Berlin»). Сравниваем по канону, а показываем как написано.
    seen: set[str] = set()
    for part in _LOCATION_LIST_SPLIT_RE.split(location):
        part = part.strip()
        if not part or is_country_wide_location(part) or is_remote_worldwide_location([part]):
            continue
        city = normalize_location(part)
        if not city:
            continue
        key = canonical_city(city) or city.casefold()
        if key not in seen:
            seen.add(key)
            cities.append(city)
    return tuple(cities)


# Человек разделяет города запятой, реже — точкой с запятой или слешем.
_LOCATION_LIST_SPLIT_RE = re.compile(r"[,;|/\n]+")


_REMOTE_WORLDWIDE_TOKENS: tuple[str, ...] = (
    "worldwide remote",
    "worldwide",
    "global remote",
    "international remote",
    "international remote companies",
    "remote worldwide",
    "remote only",
    "remote",
    "anywhere",
    "только удал",
    "удалён",
    "удален",
    "удалённо",
    "удаленно",
    # Russian/Ukrainian "remote work" synonyms used as saved-profile location labels.
    # Kept in sync with the remote synonyms recognised in profile_parser.py.
    "дистанц",
    "по всему миру",
    "весь мир",
)


# Названия, которыми пользователь обозначает «вся страна». Радиус к ним
# неприменим: страна — не точка, и отмерять от неё километры не от чего.
_COUNTRY_WIDE_LOCATION_TOKENS: tuple[str, ...] = (
    "deutschland",
    "germany",
    "по всей германии",
    "вся германия",
    "по германии",
)


def is_country_wide_location(location: str | None) -> bool:
    """True, когда в поле города стоит страна целиком, а не место в ней.

    Радиус в этом случае бессмыслен и раньше молча игнорировался: форма
    показывала «25 км», поиск возвращал вакансии со всей Германии, и понять,
    почему поле ни на что не влияет, было невозможно.
    """
    if not location:
        return False
    normalized = " ".join(location.strip().lower().split())
    if not normalized:
        return False
    parts = [part.strip() for part in re.split(r"[,;/|]", normalized) if part.strip()]
    return bool(parts) and all(part in _COUNTRY_WIDE_LOCATION_TOKENS for part in parts)


def is_remote_worldwide_location(locations: list[str] | tuple[str, ...] | None) -> bool:
    """True when locations describe global/remote search, not a local geography filter."""
    if not locations:
        return False
    normalized = " | ".join(location.strip().lower() for location in locations if location.strip())
    return any(token in normalized for token in _REMOTE_WORLDWIDE_TOKENS)


def normalize_search_location_for_profile(
    locations: list[str] | tuple[str, ...] | None,
    *,
    relocation_ready: bool | None = None,
) -> str | None:
    """Return job-board location prefill for a saved profile.

    Remote/worldwide profiles must not be narrowed to Deutschland just because the
    user lives in Germany or listed Germany among remote-friendly markets.
    """
    if is_remote_worldwide_location(locations):
        return "remote"
    # Все города профиля, а не первый: профиль «Росток, Штральзунд, Грайфсвальд»
    # раньше подставлял в форму один Росток, и два города молча терялись ещё до
    # поиска. Порядок сохраняется — он задаёт порядок выдачи.
    normalized = normalize_locations(locations)
    if normalized:
        return ", ".join(normalized)
    # Городов нет, но локация есть — это "вся Германия". Поле формы её понимает
    # и ищет по стране, поэтому подставляем как есть, а не стираем.
    return normalize_location_from_list(list(locations) if locations else None)


def normalize_query_from_roles(roles: list[str] | None) -> str | None:
    """Переводим первую роль в немецкий keyword. Если уже латиница — оставляем."""
    if not roles:
        return None
    role = roles[0].strip()
    result = ROLE_TO_DE_QUERY.get(role.lower(), role)
    # Берём первое слово — BA API надёжнее работает с одним keyword
    return result.split()[0] if result else None
