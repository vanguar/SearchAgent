"""Детерминированная нормализация поисковых полей профиля.

Используется в сервисном слое (intake, edit) и в web-роутах.
Не зависит ни от каких внешних сервисов — только статические словари.
"""
from __future__ import annotations

# Русские/украинские названия городов → официальное немецкое написание
CITY_TO_DE: dict[str, str] = {
    "берлин": "Berlin", "берлін": "Berlin",
    "гамбург": "Hamburg",
    "мюнхен": "München",
    "кёльн": "Köln", "кельн": "Köln",
    "франкфурт": "Frankfurt",
    "дортмунд": "Dortmund",
    "штутгарт": "Stuttgart",
    "лейпциг": "Leipzig",
    "дрезден": "Dresden",
    "дюссельдорф": "Düsseldorf",
    "бремен": "Bremen",
    "нюрнберг": "Nürnberg", "нюрнберґ": "Nürnberg",
    "бонн": "Bonn",
    "мангейм": "Mannheim",
    "карлсруэ": "Karlsruhe", "карлсруе": "Karlsruhe",
    "аугсбург": "Augsburg",
    "эссен": "Essen",
    "дуйсбург": "Duisburg",
    "бохум": "Bochum",
    "вупперталь": "Wuppertal",
    "билефельд": "Bielefeld", "білефельд": "Bielefeld",
    "мюнстер": "Münster",
    "ганновер": "Hannover",
    "аахен": "Aachen",
    "гейдельберг": "Heidelberg",
    "эрфурт": "Erfurt",
    "росток": "Rostock",
    "вся германия": "Deutschland",
    "по всей германии": "Deutschland",
    "deutschland": "Deutschland",
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
}


def normalize_location(location: str | None) -> str | None:
    """Переводим одно название города в немецкое. Если уже немецкое — оставляем."""
    if not location:
        return None
    return CITY_TO_DE.get(location.strip().lower(), location.strip()) or None


def normalize_location_from_list(locations: list[str] | None) -> str | None:
    """Берём первую локацию из списка и нормализуем."""
    if not locations:
        return None
    return normalize_location(locations[0])


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
    if relocation_ready is False and locations:
        # If there is no remote/global signal, keep the first explicit place.
        return normalize_location_from_list(list(locations))
    return normalize_location_from_list(list(locations) if locations else None)


def normalize_query_from_roles(roles: list[str] | None) -> str | None:
    """Переводим первую роль в немецкий keyword. Если уже латиница — оставляем."""
    if not roles:
        return None
    role = roles[0].strip()
    result = ROLE_TO_DE_QUERY.get(role.lower(), role)
    # Берём первое слово — BA API надёжнее работает с одним keyword
    return result.split()[0] if result else None
