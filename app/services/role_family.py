"""Deterministic role-family classification for query intent preservation.

Classifies both Russian query/role text and normalized German vacancy titles
into one of the known role families. Used by the fallback ladder and the
profession-family mismatch filter to prevent cross-family relevance pollution.
"""
from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache

from app.services.hashers import normalize_text_for_fingerprint


class RoleFamily(str, Enum):
    IT = "it"
    WAREHOUSE = "warehouse"
    PRODUCTION = "production"
    CLEANING = "cleaning"
    KITCHEN = "kitchen"
    CONSTRUCTION = "construction"
    AGRICULTURE = "agriculture"
    OFFICE = "office"
    SALES = "sales"
    HEALTHCARE = "healthcare"
    DRIVING = "driving"
    # Автомобильная логистика: перегон и перестановка автомобилей, работа на
    # площадке автологистического центра. Отдельно от DRIVING,
    # потому что это НЕ дорожные перевозки груза: транспорт здесь — сам товар, а
    # не средство доставки, и требования к вакансии другие (категория B, а не C/CE).
    VEHICLE_LOGISTICS = "vehicle_logistics"
    SECURITY = "security"
    GENERIC = "generic"


# Families where cross-family broad fallback is inappropriate.
# For these specific high-skill or domain-specific families, appending generic
# low-barrier manual-labor fallback keywords would inject irrelevant results.
_FAMILY_PROHIBITS_BROAD_FALLBACK: frozenset[RoleFamily] = frozenset({
    RoleFamily.IT,
    RoleFamily.HEALTHCARE,
    RoleFamily.OFFICE,
    RoleFamily.SALES,
    RoleFamily.SECURITY,
    RoleFamily.KITCHEN,
    RoleFamily.DRIVING,  # delivery/driving stays in its own family, not generic labor
    # Перегон автомобилей — узкое направление: расширять его до «helfer» значит
    # подменять запрос на другую работу.
    RoleFamily.VEHICLE_LOGISTICS,
})

# Undirected cross-family compatibility pairs (excluding self-compatibility and GENERIC).
# Symmetric by construction: frozenset({A, B}) == frozenset({B, A}).
_COMPATIBLE_SPECIFIC_PAIRS: frozenset[frozenset[RoleFamily]] = frozenset({
    frozenset({RoleFamily.IT, RoleFamily.OFFICE}),
    frozenset({RoleFamily.WAREHOUSE, RoleFamily.PRODUCTION}),
    frozenset({RoleFamily.PRODUCTION, RoleFamily.CONSTRUCTION}),
    frozenset({RoleFamily.OFFICE, RoleFamily.SALES}),
    # Пары с VEHICLE_LOGISTICS нет намеренно. Перегон автомобилей и доставка
    # посылок — разная работа: в первой транспорт это товар, во второй — средство
    # развозки по десяткам адресов. Совместимость семейств означала бы, что запрос
    # «Fahrzeugüberführer» приносит Paketzusteller, а запрос «курьер» — перегон.
    # Кому нужно и то и другое, перечисляет обе роли в своём профиле: набор
    # семейств профиля складывается из всех его ролей.
})

# Семейства, для которых работают правила «профиль водителя»: ограничение по
# категории прав и отсечение тяжёлого транспорта. Перегон сюда входит — человек
# с категорией B не сможет перегонять седельный тягач.
DRIVING_LIKE_FAMILIES: frozenset[RoleFamily] = frozenset({
    RoleFamily.DRIVING,
    RoleFamily.VEHICLE_LOGISTICS,
})

# Precompiled word-boundary pattern for "it" as a standalone token.
# Without boundaries, "it" would false-positive inside "quality", "transit", etc.
_IT_WORD_RE: re.Pattern[str] = re.compile(r"\bit\b")

# Ordered list of (family, Russian substring tokens) for query/role classification.
# First match wins; order places high-skill/distinctive families first to avoid
# lower-priority tokens being shadowed.
# NOTE: "it" is intentionally absent here — it is matched via _IT_WORD_RE before
# this loop to prevent false positives inside longer words.
_QUERY_RU_TOKENS: list[tuple[RoleFamily, tuple[str, ...]]] = [
    (RoleFamily.IT, (
        "программ", "разработ", "software", "python", "java",
        "developer", "devops", "helpdesk", "техподдержк", "автомат", "сисадм",
        "ai automation", "ai tools", "llm", "agentic", "prompt engineer",
        "claude code", "codex", "ki automatisierung",
    )),
    (RoleFamily.HEALTHCARE, ("медсестра", "медицин", "санитар", "сиделк")),
    (RoleFamily.KITCHEN, ("повар", "кухн", "кухон", "официант", "гастроном", "ресторан")),
    (RoleFamily.CLEANING, ("уборщик", "уборк", "клинер", "подготовка автомобил", "предпродажная подготовка")),
    (RoleFamily.AGRICULTURE, ("сельск", "садовник", "агроном")),
    (RoleFamily.SECURITY, ("охранник", "секьюрити")),
    (RoleFamily.OFFICE, ("бухгалтер", "секретарь", "офис-менеджер", "делопроизводств")),
    (RoleFamily.SALES, ("продавец", "торговый представ", "менеджер по продаж")),
    # Перегон стоит ВЫШЕ вождения: «перегон автомобилей» — это не доставка и не
    # развозка, и подменять его водительским запросом значит искать другую работу.
    (RoleFamily.VEHICLE_LOGISTICS, (
        "перегон", "перегонщик", "перегін", "переганя",
        "автологистик", "автологістик", "автовоз",
    )),
    (RoleFamily.DRIVING, ("водитель", "курьер", "доставк", "развоз")),
    (RoleFamily.CONSTRUCTION, (
        "строитель", "монтажник", "электрик", "сварщик", "слесарь", "сантехник", "плотник",
    )),
    (RoleFamily.WAREHOUSE, (
        "склад", "логистик", "комплектовщик", "кладовщик", "грузчик", "упаков",
    )),
    (RoleFamily.PRODUCTION, ("производ", "сборщик", "оператор станка")),
]

# Ordered list of (family, German substring tokens) for normalized job title classification.
# Input is the output of normalize_text_for_fingerprint() — lowercase ASCII, no special chars.
# More specific compound forms are listed before shorter tokens to reduce false positives.
_TITLE_DE_TOKENS: list[tuple[RoleFamily, tuple[str, ...]]] = [
    (RoleFamily.IT, (
        "softwareentwickler", "software", "entwickler", "developer",
        "programm", "python", "devops", "it support", "it-support",
        "helpdesk", "frontend", "backend", "sysadmin",
        "administrator", "systemadministrator", "servicedesk",
        "support engineer", "support specialist", "technical support",
        "it-techniker", "servicetechniker it",
        "ai automation", "ai tools", "llm", "agentic", "prompt engineer",
        "claude code", "codex", "ki automatisierung",
    )),
    # Семейство задаёт ГЛАВНОЕ СЛОВО роли, а не предметная область рядом с ним.
    # "Auslieferungsfahrer Medizinprodukte" — это водитель, который возит
    # медизделия, а не медработник; "Staplerfahrer" — складской работник, а не
    # водитель. Поэтому явные названия профессий стоят ВЫШЕ предметных семейств
    # (медицина, кухня, уборка), которые в таких заголовках описывают лишь груз
    # или место работы.
    # Погрузчик — складская работа, а не дорожная, хотя название кончается на
    # "-fahrer". Запись стоит ДО водительской именно поэтому: иначе складской
    # профиль отвергал бы Staplerfahrer как чужое семейство, а водительский
    # профиль принимал бы его за свою вакансию.
    (RoleFamily.WAREHOUSE, (
        "gabelstaplerfahrer", "schubmaststaplerfahrer", "staplerfahrer",
        "hochregalstaplerfahrer", "stapler", "hubwagen",
    )),
    # Автомобильная логистика. Записи стоят ДО водительской, потому что в этих
    # заголовках "-fahrer" — лишь хвост композита, а само занятие называет его
    # начало: "Überführungsfahrer" — перегонщик, а не водитель развозки.
    # Берутся только однозначные составные формы: голое "fahrzeug" притягивает
    # Fahrzeugbau (производство) и Fahrzeugtechniker (автомеханик).
    (RoleFamily.VEHICLE_LOGISTICS, (
        "fahrzeuguberfuhrer", "uberfuhrungsfahrer", "fahrzeuguberfuhrung", "uberfuhrung",
        "fahrzeugverbringung", "fahrzeugruckfuhrung", "ruckfuhrungsfahrer",
        "fahrzeugumsetzer", "umsetzfahrer", "fahrzeuglogistik", "autologistik",
        "pkw rangierer", "fahrzeugrangierer", "rangierer", "rangierfahrer",
        "werkstattfahrer", "hol und bringservice", "hol und bring",
        # Только в связке с ролью: голое "autovermietung" — это отрасль
        # работодателя, и "Sachbearbeiter Autovermietung" остаётся офисной работой.
        "fahrer autovermietung", "mitarbeiter autovermietung",
        "fahrzeugtransport", "autotransport", "fahrzeugzustellung", "fahrzeugtransfer", "uberfuhrer",
    )),
    (RoleFamily.DRIVING, (
        "kraftfahrer", "lieferfahrer", "zusteller", "kurier",
        "delivery driver", "truck driver", "van driver", "courier", "chauffeur",
        "fahrpersonal", "fahrdienst", "auslieferung", "fahrzeugfuhrer",
        "fahrer",  # short token after compound forms
        "driver",  # English counterpart of the short token above
    )),
    (RoleFamily.HEALTHCARE, (
        "pflegekraft", "pflegehelferin", "altenpflege", "krankenschwester", "krankenhaus",
        "pflege", "medizin", "sanitater",
    )),
    (RoleFamily.KITCHEN, (
        "kuchenhelfer", "kuchenhilfe", "kuchenleiter",
        "kuche", "gastronom", "kochin", "kellner", "restaurant", "catering",
        "koch",  # short token listed after compound forms
    )),
    (RoleFamily.CLEANING, (
        "fahrzeugaufbereit", "fahrzeugpfleg", "autoaufbereit",
        "reinigungskraft", "hausreinigung", "reinigung", "housekeeping",
    )),
    (RoleFamily.AGRICULTURE, ("landwirtschaft", "gartner", "ernte", "garten")),
    (RoleFamily.SECURITY, (
        "sicherheitsmitarbeiter", "sicherheitsdienst", "security", "wachschutz", "bewachung",
    )),
    (RoleFamily.OFFICE, (
        "buchhalter", "buchhaltung", "sachbearbeiter", "assistenz", "verwaltung", "kaufm", "controlling",
        "customer support", "customer service", "support specialist", "kundenservice", "kundenbetreuung",
    )),
    (RoleFamily.SALES, (
        "vertriebsmitarbeiter", "kundenberater", "vertrieb", "verkaufer", "einzelhandel", "kassierer",
        "customer success", "client success", "account manager", "account executive", "business development",
    )),
    (RoleFamily.CONSTRUCTION, (
        "bauhelfer", "baustelle", "zimmerer", "maurer", "elektriker", "schlosser", "monteur", "trockenbau",
    )),
    (RoleFamily.WAREHOUSE, (
        "lagermitarbeiter", "lagerhelfer", "lagerist", "kommissionier",
        "intralogistik", "lagerverwaltung",
        # Только составные английские формы: голое "warehouse" притягивает
        # "data warehouse" из IT-объявлений — ровно та ошибка, от которой уже
        # защищается POSITIVE_ROLE_FAMILIES в rule_catalog.
        "warehouse associate", "warehouse worker", "warehouse operative",
        "warehouse clerk", "warehouse assistant", "order picker", "forklift",
        "lager",  # short token after compound forms
    )),
    (RoleFamily.PRODUCTION, (
        "produktionsmitarbeiter", "produktionshelfer", "maschinenbediener", "maschinenfuhrer",
        "produktion", "fertigung",
    )),
]

# Семейства, которые побеждают в заголовке независимо от позиции слова.
#
# Обычное правило — «решает слово, стоящее раньше», — здесь даёт неверный ответ:
# в «Fahrer Fahrzeuglogistik» раньше стоит "fahrer", и вакансия автологистического
# центра попадала в доставку. Слова этого семейства — однозначные составные формы
# ("fahrzeuglogistik", "uberfuhrung", "werkstattfahrer"), и случайно они в
# заголовке не появляются: если они есть, работа именно такая, где бы они ни стояли.
_POSITION_INDEPENDENT_FAMILIES: frozenset[RoleFamily] = frozenset({RoleFamily.VEHICLE_LOGISTICS})


def classify_query_ru(text: str) -> RoleFamily:
    """Classify a Russian role or query text into a RoleFamily.

    Uses substring matching against known Russian role tokens.
    The short token "it" is matched with word boundaries to avoid false positives
    inside longer words (e.g. "quality", "transit").
    Returns GENERIC when no specific family is detected.
    """
    normalized = text.strip().lower()
    if not normalized:
        return RoleFamily.GENERIC
    # Boundary-safe check for "it" as a standalone word
    if _IT_WORD_RE.search(normalized):
        return RoleFamily.IT
    for family, tokens in _QUERY_RU_TOKENS:
        if any(token in normalized for token in tokens):
            return family
    return RoleFamily.GENERIC


def classify_vacancy_de(normalized_title: str) -> RoleFamily:
    """Classify a normalized German job title into a RoleFamily.

    Takes the output of normalize_text_for_fingerprint(canonical.normalized_title).
    Classifies based on the job title only (not body text) to stay conservative
    and avoid false positives from incidental mentions in descriptions.
    Returns GENERIC when the title does not clearly indicate a specific family.

    Побеждает слово, стоящее в заголовке РАНЬШЕ, а не семейство, стоящее выше в
    списке. Немецкий заголовок начинается с главного слова роли и дополняет его
    справа: "Kommissionierer mit Fahrertätigkeiten" — складская вакансия с
    элементами вождения, а не водительская, и водительскому профилю она не нужна.
    При равной позиции решает порядок списка: в "Staplerfahrer" и "stapler", и
    "fahrer" начинаются с нуля, и складская запись стоит выше не случайно.

    Исключение — семейства из _POSITION_INDEPENDENT_FAMILIES: их слова однозначны
    сами по себе и побеждают из любой позиции.
    """
    best_position: int | None = None
    best_family = RoleFamily.GENERIC
    for family, tokens in _TITLE_DE_TOKENS:
        positions = [position for token in tokens if (position := normalized_title.find(token)) != -1]
        if not positions:
            continue
        if family in _POSITION_INDEPENDENT_FAMILIES:
            return family
        earliest = min(positions)
        if best_position is None or earliest < best_position:
            best_position, best_family = earliest, family
    return best_family


def classify_role_text(text: str) -> RoleFamily:
    """Classify a single profile role written in ANY of the supported languages.

    Роли профиля пользователь пишет как угодно: "склад", "Lagerarbeiter",
    "Driver B - Fernverkehr". Русский классификатор понимает только первое, и
    раньше этого было достаточно, потому что роли приходили из русского intake.
    Сейчас профили хранят немецкие и английские названия, и для них
    classify_query_ru возвращал GENERIC — а GENERIC отключает межсемейный
    фильтр целиком, из-за чего складскому профилю прилетала автомастерская,
    а водительскому — погрузчик.
    """
    normalized = text.strip().lower()
    if not normalized:
        return RoleFamily.GENERIC

    russian_family = classify_query_ru(normalized)
    if is_specific_family(russian_family):
        return russian_family
    return classify_vacancy_de(normalize_text_for_fingerprint(normalized))


@lru_cache(maxsize=512)
def classify_role_families(texts: tuple[str, ...]) -> frozenset[RoleFamily]:
    """Семейства, представленные набором названий ролей.

    Кэшируется: набор ролей на прогон один, а спрашивают его на каждую вакансию
    по нескольку раз (гейт положительных сигналов, межсемейный фильтр, проверка
    отрицательных попаданий).
    """
    return frozenset(classify_role_text(text) for text in texts if text.strip())


def classify_desired_roles(roles: tuple[str, ...]) -> frozenset[RoleFamily]:
    """Return the set of RoleFamilies represented by the profile's desired roles."""
    return classify_role_families(tuple(roles))


def is_specific_family(family: RoleFamily) -> bool:
    """True when the family represents a specific domain, not GENERIC."""
    return family is not RoleFamily.GENERIC


def families_are_compatible(query_family: RoleFamily, vacancy_family: RoleFamily) -> bool:
    """True when a vacancy of vacancy_family is acceptable for a query of query_family."""
    if query_family is RoleFamily.GENERIC or vacancy_family is RoleFamily.GENERIC:
        return True
    if query_family is vacancy_family:
        return True
    return frozenset({query_family, vacancy_family}) in _COMPATIBLE_SPECIFIC_PAIRS


def prohibits_broad_fallback(family: RoleFamily) -> bool:
    """True when this query family must not expand into generic low-barrier fallback keywords.

    High-skill and domain-specific families should stay within their own family during
    fallback rather than degrading to generic manual-labor roles.
    """
    return family in _FAMILY_PROHIBITS_BROAD_FALLBACK
