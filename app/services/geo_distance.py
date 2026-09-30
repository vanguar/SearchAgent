"""Детерминированное офлайн-расстояние между немецкими населёнными пунктами.

Внешних геокодеров в рантайме нет намеренно: PROJECT_BRIEF требует local-first и
детерминированного ядра. Координаты берутся из приложенного к репозиторию среза
почтовых данных GeoNames (`app/data/germany_postal_geo.tsv.gz`, CC BY 4.0):
10 813 почтовых индексов и 16 354 названия — вся Германия, а не выборка городов.

Точка отсчёта здесь нигде не зашита. Её всегда передаёт вызывающий код, беря
город проживания из профиля, поэтому переезд пользователя меняет результат сам
собой, без правок в этом модуле.

Порядок разрешения координат — от точного к грубому:

1. Точное название населённого пункта.
2. Пятизначный почтовый индекс.
3. Двузначная почтовая зона (центроид) — ошибка до ~50 км, последняя попытка
   для написаний, которых нет в датасете.
4. Ничего не найдено — возвращается None. Вызывающий код обязан трактовать это
   как «расстояние неизвестно», а не как ноль.

`canonical_city()` дополнительно сводит городские районы к самому городу:
Evershagen, Biestow и Brinckmansdorf — это Росток, и для дедупликации вакансий
они должны выглядеть одинаково. Районов нет в почтовом датасете (он оперирует
муниципалитетами), поэтому этот слой ведётся вручную и пополняется по мере того,
как источники приносят новые написания.
"""
from __future__ import annotations

import gzip
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_EARTH_RADIUS_KM = 6371.0088
_POSTAL_CODE_RE = re.compile(r"\b(\d{5})\b")
_GEO_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "germany_postal_geo.tsv.gz"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class _WeightedPlace:
    """Место и его вес — число почтовых индексов, приходящихся на это место."""

    point: GeoPoint
    weight: int


# Районы (Ortsteile), которые источники подставляют вместо названия города.
_CITY_DISTRICT_PARENTS: dict[str, str] = {
    # Росток
    "warnemunde": "rostock",
    "evershagen": "rostock",
    "biestow": "rostock",
    "brinckmansdorf": "rostock",
    "gartenstadt": "rostock",
    "diedrichshagen": "rostock",
    "lutten klein": "rostock",
    "lichtenhagen": "rostock",
    "gross klein": "rostock",
    "schmarl": "rostock",
    "reutershagen": "rostock",
    "toitenwinkel": "rostock",
    "dierkow": "rostock",
    "gehlsdorf": "rostock",
    "sudstadt": "rostock",
    "hansaviertel": "rostock",
    "stadtmitte": "rostock",
    "kropeliner tor vorstadt": "rostock",
    "markgrafenheide": "rostock",
    "hinrichsdorf": "rostock",
    "krummendorf": "rostock",
    "nienhagen bei rostock": "rostock",
    "kassebohm": "rostock",
    "seebad warnemunde": "rostock",
    "riekdahl": "rostock",
    "bramow": "rostock",
    "komponistenviertel": "rostock",
    # Гамбург
    "altona": "hamburg",
    "wandsbek": "hamburg",
    "bergedorf": "hamburg",
    "eimsbuttel": "hamburg",
    "barmbek": "hamburg",
    "st pauli": "hamburg",
    "hamburg mitte": "hamburg",
    "hamburg nord": "hamburg",
    "bahrenfeld": "hamburg",
    "eidelstedt": "hamburg",
    "fuhlsbuttel": "hamburg",
    "hausbruch": "hamburg",
    "billwerder": "hamburg",
    "gut moor": "hamburg",
    "hamburg bezirk harburg": "hamburg",
    "hamburg bezirk altona": "hamburg",
    "hamburg bezirk wandsbek": "hamburg",
    "hamburg bezirk bergedorf": "hamburg",
    "altona altstadt": "hamburg",
    "wohldorf ohlstedt": "hamburg",
    "billstedt": "hamburg",
    "winterhude": "hamburg",
    "ottensen": "hamburg",
    "rahlstedt": "hamburg",
    "stellingen": "hamburg",
    # Берлин
    # https://www.berlin.de/ba-treptow-koepenick/ueber-den-bezirk/ortsteile/adlershof/
    "adlershof": "berlin",
    "charlottenburg": "berlin",
    "kreuzberg": "berlin",
    "neukolln": "berlin",
    "pankow": "berlin",
    "spandau": "berlin",
    "steglitz": "berlin",
    "tempelhof": "berlin",
    "friedrichshain": "berlin",
    "lichtenberg": "berlin",
    "marzahn": "berlin",
    "reinickendorf": "berlin",
    "treptow": "berlin",
    "kopenick": "berlin",
    "lichtenrade": "berlin",
    "marienfelde": "berlin",
    "mariendorf": "berlin",
    "wedding": "berlin",
    "moabit": "berlin",
    "prenzlauer berg": "berlin",
    "nikolassee": "berlin",
    "zehlendorf": "berlin",
    "wilmersdorf": "berlin",
    "schoneberg": "berlin",
    "gesundbrunnen": "berlin",
}

# Районы мегаполисов, чьё имя носит ЕЩЁ И отдельный населённый пункт в другой
# части Германии.
#
# Почтовый справочник работает на уровне муниципалитетов, поэтому берлинских
# Ortsteile в нём нет вовсе: запись «Biesdorf» — это Бисдорф в Айфеле (576 км от
# Берлина), «Weissensee» — тюрингский город (219 км), «Rosenthal» — гессенский
# (357 км). Из-за этого голое «Biesdorf» в объявлении уверенно опознавалось как
# другой город, и берлинская вакансия жёстко отклонялась.
#
# Что мы про такое имя знаем на самом деле: ничего. Это либо район заказанного
# города, либо одноимённый город в шестистах километрах, и по одному слову их не
# различить. Поэтому ответ здесь — «по локации не понять»: вакансия остаётся в
# выдаче, но городом не помечается. Потерять живую вакансию хуже, чем показать
# одну лишнюю; а когда у поиска задан радиус, лишнюю всё равно отсечёт проверка
# расстояния.
_AMBIGUOUS_DISTRICT_NAMES: dict[str, frozenset[str]] = {
    "berlin": frozenset({
        "hansaviertel", "tiergarten", "weissensee", "blankenburg", "heinersdorf", "karow",
        "blankenfelde", "buch", "franzosisch buchholz", "rosenthal", "wilhelmsruh",
        "schmargendorf", "grunewald", "westend", "halensee", "haselhorst", "siemensstadt",
        "staaken", "gatow", "kladow", "hakenfelde", "wilhelmstadt", "lichterfelde", "lankwitz",
        "dahlem", "wannsee", "schlachtensee", "friedenau", "britz", "buckow", "rudow",
        "gropiusstadt", "planterwald", "baumschulenweg", "johannisthal", "altglienicke",
        "bohnsdorf", "friedrichshagen", "rahnsdorf", "grunau", "muggelheim", "schmockwitz",
        "biesdorf", "kaulsdorf", "mahlsdorf", "friedrichsfelde", "karlshorst", "falkenberg",
        "malchow", "wartenberg", "tegel", "konradshohe", "heiligensee", "frohnau", "hermsdorf",
        "waidmannslust", "lubars", "wittenau", "borsigwalde",
    }),
    "hamburg": frozenset({
        "allermohe", "alsterdorf", "altengamme", "altenwerder", "bahrenfeld", "billbrook",
        "borgfelde", "curslack", "dulsberg", "eilbek", "eppendorf", "finkenwerder", "francop",
        "hamm", "harvestehude", "hoheluft", "horn", "jenfeld", "langenhorn", "lokstedt",
        "lurup", "moorburg", "neuenfelde", "neugraben", "niendorf", "ohlsdorf", "osdorf",
        "rissen", "rothenburgsort", "schnelsen", "steilshoop", "sulldorf", "tonndorf",
        "uhlenhorst", "veddel", "wilhelmsburg", "wohldorf",
    }),
}


def _is_ambiguous_district(candidate: str, target: str) -> bool:
    """Может ли это имя быть районом заказанного города, а может и чужим городом."""
    return candidate in _AMBIGUOUS_DISTRICT_NAMES.get(target, frozenset())


# Страны, названные в поле локации. Справочник тут только немецкий, поэтому
# «Kyiv, Ukraine» или «Wien, Österreich» не опознавались ни как заказанный город,
# ни как чужой, и вакансия оставалась в выдаче по Берлину — при том что это
# заведомо не Берлин. Названная страна, кроме Германии, отвечает однозначно.
_NON_GERMAN_COUNTRY_MARKERS: tuple[str, ...] = (
    "ukraine", "ukraina", "osterreich", "austria", "schweiz", "switzerland", "suisse",
    "polska", "polen", "poland", "czechia", "tschechien", "czech republic", "slowakei",
    "slovakia", "ungarn", "hungary", "rumanien", "romania", "bulgarien", "bulgaria",
    "niederlande", "netherlands", "holland", "belgien", "belgium", "luxemburg",
    "luxembourg", "frankreich", "france", "spanien", "spain", "portugal", "italien",
    "italy", "danemark", "denmark", "schweden", "sweden", "norwegen", "norway",
    "finnland", "finland", "estland", "estonia", "lettland", "latvia", "litauen",
    "lithuania", "irland", "ireland", "united kingdom", "great britain", "england",
    "schottland", "scotland", "turkei", "turkey", "griechenland", "greece", "kroatien",
    "croatia", "slowenien", "slovenia", "serbien", "serbia", "united states", "usa",
    "canada", "kanada", "india", "indien", "brazil", "brasilien", "kasachstan",
    "kazakhstan", "georgien", "georgia", "moldau", "moldova", "uzbekistan",
    "usbekistan", "kirgisistan", "kyrgyzstan",
)
_GERMANY_MARKERS: frozenset[str] = frozenset({"deutschland", "germany", "germania", "de"})


def names_other_country(location_text: str | None, *, country_code: str | None = None) -> bool:
    """Названа ли в локации страна, отличная от Германии.

    Смотрит ТОЛЬКО на поле локации, не на текст объявления: немецкая вакансия
    вполне может упоминать другие страны в описании, и это ничего не значит.

    Явное «Deutschland» рядом перебивает: «Frankfurt, Deutschland, Kunden in
    Polen» — это немецкая вакансия.
    """
    if country_code and country_code.upper() != "DE":
        return True
    if not location_text:
        return False
    normalized = _ascii_fold(location_text)
    if not normalized:
        return False
    tokens = set(normalized.split())
    if tokens & _GERMANY_MARKERS:
        return False
    padded = f" {normalized} "
    return any(f" {marker} " in padded for marker in _NON_GERMAN_COUNTRY_MARKERS)


# Псевдонимы названий. Две группы:
#  * английские экзонимы — часть источников (Arbeitnow, Remotive, Greenhouse)
#    пишет города по-английски, а в почтовом датасете они только по-немецки;
#  * обиходные краткие формы — в объявлениях пишут "Frankfurt" и "Freiburg",
#    тогда как официальное имя в датасете полное, и без псевдонима крупный город
#    просто не находится.
_CITY_EXONYMS: dict[str, str] = {
    "frankfurt": "frankfurt am main",
    "freiburg": "freiburg im breisgau",
    "ludwigshafen": "ludwigshafen am rhein",
    "brandenburg": "brandenburg an der havel",
    "mulheim an der ruhr": "mulheim an der ruhr",
    "halle saale": "halle",
    "wittenberg": "lutherstadt wittenberg",
    "munich": "munchen",
    "cologne": "koln",
    "nuremberg": "nurnberg",
    "hanover": "hannover",
    "brunswick": "braunschweig",
    "frankfurt on the main": "frankfurt am main",
    "westphalia": "westfalen",
    "bavaria": "bayern",
    "lower saxony": "niedersachsen",
    "saxony": "sachsen",
}

# Центроиды федеральных земель. Источники иногда дают только землю
# ("Sachsen-Anhalt"), и это не город — но и не повод считать расстояние
# неизвестным: в масштабе страны ошибка в сто километров лучше, чем пропуск,
# из-за которого вакансия без локации обходит в рейтинге вакансию с локацией.
_STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "baden wurttemberg": (48.66, 9.35),
    "bayern": (48.95, 11.40),
    "berlin": (52.52, 13.40),
    "brandenburg": (52.40, 13.06),
    "bremen": (53.08, 8.80),
    "hamburg": (53.55, 9.99),
    "hessen": (50.60, 9.00),
    "mecklenburg vorpommern": (53.75, 12.60),
    "niedersachsen": (52.75, 9.40),
    "nordrhein westfalen": (51.45, 7.40),
    "rheinland pfalz": (49.95, 7.45),
    "saarland": (49.38, 6.97),
    "sachsen": (51.05, 13.35),
    "sachsen anhalt": (51.95, 11.70),
    "schleswig holstein": (54.20, 9.80),
    "thuringen": (50.90, 11.03),
}

# Приставки и хвосты, которые источники приклеивают к названию города.
_CITY_NOISE_PREFIX_RE = re.compile(r"^(?:raum|region|grossraum|nahe|naehe|bei|umkreis|umgebung)\s+")
_CITY_NOISE_SUFFIX_RE = re.compile(
    r"\s+(?:und\s+umgebung|und\s+umland|umgebung|umland|"
    r"mecklenburg\s+vorpommern|schleswig\s+holstein|nordrhein\s+westfalen|"
    r"baden\s+wurttemberg|sachsen\s+anhalt|rheinland\s+pfalz|niedersachsen|"
    r"brandenburg|bayern|hessen|sachsen|thuringen|saarland|deutschland|germany)$"
)
# "Roggentin bei Rostock", "Freiburg im Breisgau" — город стоит слева от уточнения.
_CITY_QUALIFIER_RE = re.compile(r"\s+(?:bei|an|am|im|in|auf|ob|vor)\s+.+$")
_CITY_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_LOCATION_SEGMENT_SPLIT_RE = re.compile(r"[,;/|]")
_CITY_QUALIFIER_CAPTURE_RE = re.compile(r"\s+(?:bei|an|am|im|in|vor)\s+(?P<anchor>.+)$")
# Тёзки, разбросанные дальше этого, — разные места, и без уточнения выбрать
# между ними нельзя.
_AMBIGUOUS_PLACE_MAX_SPREAD_KM = 30.0
# Во сколько раз место должно быть крупнее тёзки, чтобы считаться очевидным.
_DOMINANT_PLACE_WEIGHT_RATIO = 3


@lru_cache(maxsize=1)
def _geo_tables() -> tuple[dict[str, GeoPoint], dict[str, tuple[_WeightedPlace, ...]], dict[str, GeoPoint]]:
    """Таблицы (индекс → точка, название → ВСЕ его места, почтовая зона → центроид).

    Название хранит список, а не одну точку: в Германии полно тёзок, и два
    Роггентина лежат в 80 км друг от друга. Ресурс читается один раз за процесс.
    Отсутствие файла не должно ронять поиск — расстояние просто станет
    неизвестным для всех вакансий.
    """
    postal: dict[str, GeoPoint] = {}
    place_lists: dict[str, list[_WeightedPlace]] = {}
    if not _GEO_DATA_PATH.exists():  # pragma: no cover - защитная ветка
        return postal, {}, {}

    with gzip.open(_GEO_DATA_PATH, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            kind, key, latitude, longitude = parts[:4]
            try:
                point = GeoPoint(float(latitude), float(longitude))
                weight = int(parts[4]) if len(parts) > 4 else 1
            except ValueError:  # pragma: no cover - повреждённая строка ресурса
                continue
            if kind == "p":
                postal[key] = point
            elif kind == "c":
                place_lists.setdefault(key, []).append(_WeightedPlace(point, weight))

    prefixes: dict[str, list[GeoPoint]] = {}
    for code, point in postal.items():
        prefixes.setdefault(code[:2], []).append(point)
    prefix_centroids = {
        prefix: GeoPoint(
            sum(point.latitude for point in points) / len(points),
            sum(point.longitude for point in points) / len(points),
        )
        for prefix, points in prefixes.items()
    }
    places = {
        key: tuple(sorted(points, key=lambda place: -place.weight))
        for key, points in place_lists.items()
    }
    return postal, places, prefix_centroids


# Сколько почтовых индексов должно приходиться на место, чтобы считать его
# городом, у которого в объявлениях пишут районы.
#
# Порог низкий намеренно: форма «Город-Район» в Германии universal, а не только
# столичная — «Rostock-Evershagen» ровно того же устройства, что
# «Berlin-Kreuzberg», и у Ростока всего десять индексов. Отсекается лишь хутор с
# единственным индексом, чтобы «Neustadt Holstein» не становился «Neustadt».
_CITY_WITH_DISTRICTS_MIN_POSTAL_CODES = 2


def _parent_city_head(normalized: str) -> str | None:
    """Город, стоящий в начале составного названия района.

    Почтовый справочник называет районы «Berlin Kreuzberg», «Hamburg Altona
    Altstadt» — и это ОТДЕЛЬНЫЕ его записи. Из-за этого вакансия в Кройцберге
    считалась вакансией другого города и жёстко отклонялась при поиске по
    Берлину, хотя Кройцберг и есть Берлин.

    Правило общее: в немецком названии района муниципалитет стоит первым, а
    независимые населённые пункты повторяют чужое имя только с предлогом
    («Neustadt an der Weinstraße», «Roggentin bei Rostock»), и такие формы
    разбираются отдельно, в _CITY_QUALIFIER_RE.
    """
    tokens = normalized.split()
    if len(tokens) < 2:
        return None
    _, place_table, _ = _geo_tables()
    # Самая длинная подходящая голова: «frankfurt am main hoechst» — это
    # Франкфурт-на-Майне, а не Франкфурт вообще.
    for length in range(len(tokens) - 1, 0, -1):
        head = " ".join(tokens[:length])
        head = _CITY_DISTRICT_PARENTS.get(head, _CITY_EXONYMS.get(head, head))
        places = place_table.get(head)
        if places and sum(place.weight for place in places) >= _CITY_WITH_DISTRICTS_MIN_POSTAL_CODES:
            return head
    return None


def canonical_city(city: str | None) -> str | None:
    """Нормализованное имя города: районы сведены к своему городу.

    Возвращает ascii-ключ в нижнем регистре, пригодный и как ключ дедупликации,
    и как ключ поиска координат.
    """
    normalized = _normalize_city(city)
    if not normalized:
        return None
    normalized = _CITY_EXONYMS.get(normalized, normalized)
    district_parent = _CITY_DISTRICT_PARENTS.get(normalized)
    if district_parent is not None:
        return district_parent
    return _parent_city_head(normalized) or normalized


def is_known_place(name: str | None) -> bool:
    """Есть ли такое название в справочнике населённых пунктов.

    Отличается от canonical_city(), которая лишь нормализует строку и вернёт
    непустой результат для любого слова. Нужна там, где важно именно то, что
    слово — это место: например, чтобы отличить филиал "Randstad Deutschland"
    от другой фирмы "Meyer Bau".
    """
    normalized = canonical_city(name)
    if not normalized:
        return False
    _, place_table, _ = _geo_tables()
    return normalized in place_table


def place_weight(name: str | None) -> int:
    """Сколько почтовых индексов приходится на название — грубая мера размера.

    Нужна там, где из нескольких подходящих написаний надо выбрать одно:
    у города индексов десятки, у одноимённого хутора один.
    """
    normalized = canonical_city(name)
    if not normalized:
        return 0
    _, place_table, _ = _geo_tables()
    places = place_table.get(normalized)
    return sum(place.weight for place in places) if places else 0


def location_matches_city(location_text: str | None, city: str | None) -> bool | None:
    """В этом ли городе вакансия. None означает «по локации не понять».

    Источники пишут место по-разному: "18055 Rostock, Mecklenburg-Vorpommern"
    у BA, "Gehlsdorf, Rostock" у Adzuna, просто "Berlin" у Arbeitnow. Поэтому
    сравнивается каждый сегмент отдельно, а районы сводятся к своему городу.

    Пустое "нет" и "не знаю" здесь разные ответы: вакансию без распознанной
    локации нельзя объявить чужой — источник вернул её по запросу этого города,
    и выбрасывать её означало бы наказывать за молчание работодателя.
    """
    target = canonical_city(city)
    if not target:
        return None

    any_known_segment = False
    for segment in (location_text, *_location_segments(location_text)):
        # Имя, которое носит и район заказанного города, и чужой город, не
        # доказывает ничего — см. _AMBIGUOUS_DISTRICT_NAMES. Проверяется ДО
        # разбора вариантов: вариант мог уже увести имя в чужой город (таблица
        # районов относит «Hansaviertel» к Ростоку, хотя он есть и в Берлине).
        if _is_ambiguous_district(_normalize_city(segment), target):
            continue
        for candidate in _city_candidates(segment):
            if candidate == target:
                return True
            if _is_ambiguous_district(candidate, target):
                continue
            if is_known_place(candidate):
                any_known_segment = True
    return False if any_known_segment else None


def resolve_point(
    *,
    city: str | None = None,
    postal_code: str | None = None,
    location_text: str | None = None,
) -> GeoPoint | None:
    """Координаты локации или None, когда определить их нечем.

    Точное название города приоритетнее индекса, а индекс — приоритетнее
    свободного текста, где город может оказаться просто словом внутри фразы.
    """
    postal_table, place_table, prefix_table = _geo_tables()

    for source in (city, location_text):
        for candidate in _city_candidates(source):
            point = _pick_place(place_table.get(candidate), source, place_table)
            if point is not None:
                return point
        # Индекс точнее, чем название из свободного текста, но менее точен, чем
        # явно переданный город, поэтому проверяется между ними.
        if source is city:
            postal = _extract_postal_code(postal_code) or _extract_postal_code(location_text)
            if postal is not None and (point := postal_table.get(postal)) is not None:
                return point

    # Источники присылают локацию иерархией: Adzuna даёт "Brinckmansdorf, Rostock",
    # а нормализатор оставляет только первый сегмент. Родительский город при этом
    # известен, и терять его — значит выбрасывать расстояние на ровном месте.
    for segment in _location_segments(location_text):
        for candidate in _city_candidates(segment):
            point = _pick_place(place_table.get(candidate), segment, place_table)
            if point is not None:
                return point

    postal = _extract_postal_code(postal_code) or _extract_postal_code(location_text)
    if postal is not None and (point := prefix_table.get(postal[:2])) is not None:
        return point

    # Составные названия вида "Berlin-Bezirk Mitte" начинаются с самого города.
    # Требуем, чтобы ведущая часть была настоящим местом, иначе "Bad Homburg"
    # схлопнется в "Bad".
    for value in (city, location_text):
        point = _leading_city_point(value, place_table)
        if point is not None:
            return point

    # Последний рубеж — федеральная земля.
    for value in (city, location_text):
        for segment in (*_location_segments(value), _normalize_city(value)):
            coords = _STATE_CENTROIDS.get(segment)
            if coords is not None:
                return GeoPoint(*coords)
    return None


def _leading_city_point(
    value: str | None,
    place_table: dict[str, tuple[_WeightedPlace, ...]],
) -> GeoPoint | None:
    """Город, стоящий в начале составного названия."""
    normalized = _normalize_city(value)
    tokens = normalized.split()
    for length in range(min(len(tokens) - 1, 3), 0, -1):
        head = " ".join(tokens[:length])
        head = _CITY_DISTRICT_PARENTS.get(head, _CITY_EXONYMS.get(head, head))
        point = _pick_place(place_table.get(head), None, place_table)
        if point is not None:
            return point
    return None


def _location_segments(location_text: str | None) -> tuple[str, ...]:
    """Части составной локации, от самой узкой к самой широкой."""
    if not location_text:
        return ()
    parts = [part.strip() for part in _LOCATION_SEGMENT_SPLIT_RE.split(location_text)]
    return tuple(part for part in parts if part)


def _pick_place(
    candidates: tuple[_WeightedPlace, ...] | None,
    raw_text: str | None,
    place_table: dict[str, tuple[_WeightedPlace, ...]],
) -> GeoPoint | None:
    """Выбрать нужное место среди тёзок.

    Три ступени, от надёжной к слабой:

    1. Уточнение в самом тексте — "Roggentin bei Rostock". Берём тёзку, ближайшего
       к уточняющему городу; без этого выходила ошибка в 46 км.
    2. Явно более крупное место. Число почтовых индексов отличает город от хутора:
       "Schwerin" — это столица земли, а не одноимённая деревня под Гентином.
    3. Ничего не выделяется — возвращаем None. Неизвестное расстояние сохраняет
       вакансию в выдаче, а выдуманное молча выбрасывает её по радиусу, поэтому
       угадывать здесь дороже, чем признать незнание.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].point

    anchor = _qualifier_point(raw_text, place_table)
    if anchor is not None:
        return min(candidates, key=lambda place: distance_km(anchor, place.point) or 0.0).point

    ranked = sorted(candidates, key=lambda place: -place.weight)
    if ranked[0].weight >= ranked[1].weight * _DOMINANT_PLACE_WEIGHT_RATIO:
        return ranked[0].point

    spread = max(
        (distance_km(left.point, right.point) or 0.0)
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    )
    if spread <= _AMBIGUOUS_PLACE_MAX_SPREAD_KM:
        return GeoPoint(
            sum(place.point.latitude for place in candidates) / len(candidates),
            sum(place.point.longitude for place in candidates) / len(candidates),
        )
    return None


def _qualifier_point(
    raw_text: str | None,
    place_table: dict[str, tuple[_WeightedPlace, ...]],
) -> GeoPoint | None:
    """Координаты уточняющего города из конструкции "X bei Y"."""
    normalized = _normalize_city(raw_text)
    match = _CITY_QUALIFIER_CAPTURE_RE.search(normalized)
    if match is None:
        return None
    qualifier = _CITY_DISTRICT_PARENTS.get(match.group("anchor"), match.group("anchor"))
    return _pick_place(place_table.get(qualifier), None, place_table)


def distance_km(origin: GeoPoint | None, destination: GeoPoint | None) -> float | None:
    """Расстояние по большому кругу в километрах; None, если точка неизвестна."""
    if origin is None or destination is None:
        return None
    lat1, lon1 = math.radians(origin.latitude), math.radians(origin.longitude)
    lat2, lon2 = math.radians(destination.latitude), math.radians(destination.longitude)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    inner = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(inner))


def _ascii_fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.replace("ß", "ss")
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^0-9a-zA-Z]+", " ", folded).strip().lower()


def _normalize_city(city: str | None) -> str:
    if not city:
        return ""
    # "Berlin (Zentrale)", "München (Homeoffice)", "Rostock (Hauptsitz)" — уточнение
    # в скобках относится к офису, а не к городу. Без его отсечения Берлин в 182 км
    # от места поиска не опознавался и проходил мимо радиуса.
    normalized = _ascii_fold(_CITY_PARENTHETICAL_RE.sub(" ", city))
    if not normalized:
        return ""
    normalized = _CITY_NOISE_PREFIX_RE.sub("", normalized)
    normalized = _CITY_NOISE_SUFFIX_RE.sub("", normalized)
    # Индекс внутри названия ("18209 Bad Doberan") городом не является.
    normalized = re.sub(r"\b\d{4,5}\b", " ", normalized)
    return " ".join(normalized.split())


def _city_candidates(city: str | None) -> tuple[str, ...]:
    """Варианты написания города от наиболее к наименее точному."""
    normalized = _normalize_city(city)
    if not normalized:
        return ()

    candidates: list[str] = []

    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)

    # Таблица районов идёт ПЕРВОЙ и побеждает почтовый датасет. В Германии
    # хватает одноимённых мест: Diedrichshagen — это и район Ростока, и отдельная
    # община под Грайфсвальдом в 89 км оттуда. Источники, которые подставляют
    # название района вместо города, имеют в виду район, поэтому вакансия DRK
    # Rostock уезжала под Грайфсвальд и вылетала за радиус поиска.
    add(_CITY_DISTRICT_PARENTS.get(normalized, ""))
    add(normalized)
    add(_CITY_EXONYMS.get(normalized, ""))

    # "Roggentin bei Rostock" → "roggentin"; "Freiburg im Breisgau" → "freiburg".
    trimmed = _CITY_QUALIFIER_RE.sub("", normalized).strip()
    if trimmed != normalized:
        add(_CITY_DISTRICT_PARENTS.get(trimmed, ""))
        add(trimmed)
        add(_CITY_EXONYMS.get(trimmed, ""))

    # Район мегаполиса — это сам мегаполис. Справочник хранит "berlin kreuzberg"
    # отдельной записью, поэтому без этого варианта вакансия в Кройцберге
    # выглядела вакансией другого города и отклонялась при поиске по Берлину.
    # Вариант добавляется ПОСЛЕДНИМ: точное название всегда важнее.
    add(_parent_city_head(normalized) or "")
    return tuple(candidates)


def _extract_postal_code(value: str | None) -> str | None:
    if not value:
        return None
    match = _POSTAL_CODE_RE.search(value)
    return match.group(1) if match else None
