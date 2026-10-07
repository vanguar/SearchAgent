from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from app.core.relevance_config import get_relevance_config
from app.services.employer_branch import branch_location_conflict
from app.services.employer_identity import configured_alias, employer_key
from app.services.geo_distance import canonical_city, is_known_place
from app.services.hashers import fingerprint_tokens, normalize_text_for_fingerprint, token_similarity
from app.services.normalization_models import (
    CanonicalVacancySnapshot,
    DuplicateCandidate,
    NormalizedLocation,
    NormalizedVacancyRecord,
)
from app.services.role_family import RoleFamily, classify_vacancy_de


class VacancyDeduper:
    """Deterministic duplicate evaluation based on normalized fields and fingerprints."""

    def find_duplicate_candidate(
        self,
        record: NormalizedVacancyRecord,
        candidates: tuple[CanonicalVacancySnapshot, ...],
    ) -> DuplicateCandidate | None:
        best_candidate: DuplicateCandidate | None = None
        for candidate in candidates:
            evaluation = self.evaluate(record, candidate)
            if not evaluation.is_duplicate:
                continue
            if best_candidate is None or _candidate_rank(evaluation) > _candidate_rank(best_candidate):
                best_candidate = evaluation
        return best_candidate

    def evaluate(
        self,
        record: NormalizedVacancyRecord,
        candidate: CanonicalVacancySnapshot,
    ) -> DuplicateCandidate:
        title_similarity = max(
            token_similarity(record.title_tokens, candidate.title_tokens),
            token_similarity(_role_tokens(record), _role_tokens(candidate)),
        )
        content_similarity = token_similarity(record.content_tokens, candidate.content_tokens)
        # Сходство именно текстов объявлений: в content_tokens входят заголовок
        # и компания, и два разных текста под одним заголовком выглядят похожими.
        body_similarity = token_similarity(_body_tokens(record.body_text), _body_tokens(candidate.body_text))
        company_match = _companies_match(record.normalized_company, candidate.normalized_company)
        # Канонический ключ работодателя («DHL» = «Deutsche Post AG»). Используется
        # только правилами, которые требуют совпадения самих текстов: сокращённое
        # имя агентства без такого подтверждения склеивать небезопасно.
        employer_match = _employers_match(record.normalized_company, candidate.normalized_company)
        # Надёжное совпадение: то же имя компании или алиас из конфига
        # («Deutsche Post AG» = «Deutsche Post und DHL - Niederlassung …»).
        alias = configured_alias(record.normalized_company)
        trusted_employer = (
            company_match
            or bool(alias and alias == configured_alias(candidate.normalized_company))
            or (employer_match and not _looks_like_agency(record.normalized_company, candidate.normalized_company))
        )
        location_match = _locations_match(record, candidate)
        posting_date_close = _dates_are_close(record.posted_date, candidate.posted_date)

        reason_codes: list[str] = []
        if title_similarity >= 0.82:
            reason_codes.append("title_match_strong")
        elif title_similarity >= 0.68:
            reason_codes.append("title_match_partial")
        if company_match:
            reason_codes.append("company_match")
        if location_match:
            reason_codes.append("location_match")
        if content_similarity >= 0.58:
            reason_codes.append("content_match")
        if posting_date_close:
            reason_codes.append("posting_date_close")

        # Разные города — разные вакансии, что бы ни говорило сходство текста.
        # У кадровых агентств тело объявления шаблонное и совпадает на 90%+ между
        # филиалами: без этого запрета "Versandmitarbeiter" от Randstad в Ростоке
        # и в Муггенстурме (700 км) склеивались в одну карточку, и одна из двух
        # реальных вакансий просто исчезала из выдачи.
        locations_conflict = _locations_conflict(record, candidate)
        left_refs = set(record_employer_references(record))
        right_refs = set(candidate.employer_references) | _employer_references(candidate.body_text)
        references_conflict = bool(left_refs and right_refs and left_refs.isdisjoint(right_refs))
        ba_reference = ba_employer_reference(record)
        ba_reference_match = bool(
            (ba_reference and ba_reference in right_refs)
            or (left_refs & set(candidate.ba_reference_ids))
        )
        # An exact BA identifier explicitly cited by another source is strong identity
        # evidence even when publication dates/snippet lengths differ. Keep employer,
        # role and city safeguards; arbitrary aggregator IDs are not employer references.
        reference_duplicate = (
            ba_reference_match and title_similarity >= 0.68
            and (company_match or _agency_names_match(record.normalized_company, candidate.normalized_company))
        )
        if reference_duplicate:
            reason_codes.append("ba_employer_reference_match")
        # A shortened agency name is safe only with independent textual evidence.
        agency_duplicate = (
            title_similarity >= 0.95 and location_match and posting_date_close
            and _agency_names_match(record.normalized_company, candidate.normalized_company)
            and (
                bool(left_refs & right_refs)
                or _matching_description_prefix(record.body_text, candidate.body_text)
            )
        )
        if agency_duplicate:
            reason_codes.append("agency_alias_with_content_evidence")

        # Одна вакансия под разными названиями роли: тот же работодатель, тот же
        # текст объявления, а заголовки отличаются только словом роли
        # («Kurier» / «Lieferfahrer» / «Fahrer» … «- Berlin - Marienfelde»).
        same_posting_text = (
            not locations_conflict
            and employer_match
            and _bodies_are_the_same_posting(record, candidate, body_similarity)
            and _titles_differ_only_by_role_words(record, candidate)
        )
        if same_posting_text:
            reason_codes.append("same_employer_same_text")
        # Одинаковый заголовок и похожий текст у одного работодателя в одном
        # городе — одна карточка, даже если источник дал позициям разные номера:
        # человеку нужна одна вакансия с несколькими ссылками, а не пять
        # одинаковых. Похожесть текста обязательна: у агентства под одним
        # заголовком бывают разные клиенты с разными требованиями, и их
        # требования не должны смешиваться в одной карточке.
        same_title_same_place = (
            not locations_conflict
            and employer_match
            and location_match
            and (
                body_similarity >= 0.58
                or (
                    # Без похожего текста склеиваем только при надёжном совпадении
                    # работодателя: сокращённое имя агентства требует подтверждения
                    # текстом, а фрагменты разных источников одного объявления DHL
                    # похожими быть не обязаны.
                    trusted_employer
                    and not references_conflict
                )
            )
            and bool(_role_tokens(record))
            and set(_role_tokens(record)) == set(_role_tokens(candidate))
        )
        if same_title_same_place:
            reason_codes.append("same_employer_same_title_same_city")

        # Заголовки называют разные места («… in Berlin-Tempelhof» и без района) —
        # это разные вакансии, сколько бы ни совпадало остальное.
        places_differ = title_similarity >= 0.68 and _title_places_differ(record, candidate)
        is_duplicate = same_posting_text or same_title_same_place or (
            not locations_conflict and not references_conflict and not places_differ
        ) and (
            reference_duplicate or agency_duplicate
            or (
                title_similarity >= 0.82
                and company_match
                and (location_match or content_similarity >= 0.58)
            )
            or (
                title_similarity >= 0.9
                and company_match
                and posting_date_close
                and content_similarity >= 0.4
            )
        )

        return DuplicateCandidate(
            canonical_key=candidate.canonical_key,
            is_duplicate=is_duplicate,
            title_similarity=title_similarity,
            content_similarity=content_similarity,
            company_match=company_match,
            location_match=location_match,
            posting_date_close=posting_date_close,
            reason_codes=tuple(reason_codes or ("no_safe_duplicate_signal",)),
        )


_REFERENCE_RE = re.compile(
    r"\b(?:referenznummer|referenz\s*nr\.?|reference\s*(?:number|id)|job\s*id)\s*[:#]?\s*([a-z0-9][a-z0-9/-]{5,})",
    re.IGNORECASE,
)


def _employer_references(body: str | None) -> set[str]:
    return {match.casefold() for match in _REFERENCE_RE.findall(body or "")}


def ba_employer_reference(record: NormalizedVacancyRecord) -> str | None:
    if record.source_id == "ba" and re.fullmatch(r"\d{5,}-\d{5,}-[a-z]", record.external_id, re.IGNORECASE):
        return record.external_id.casefold()
    return None


def record_employer_references(record: NormalizedVacancyRecord) -> tuple[str, ...]:
    references = _employer_references(record.body_text)
    if reference := ba_employer_reference(record):
        references.add(reference)
    return tuple(sorted(references))


def _agency_names_match(left: str | None, right: str | None) -> bool:
    descriptors = {"arbeitsvermittlung", "personalvermittlung", "personaldienstleistungen"}
    if not left or not right:
        return False

    def core(value: str) -> tuple[str, ...]:
        return tuple(token for token in value.split() if token not in descriptors)

    return core(left) == core(right) and bool(core(left))


def _matching_description_prefix(left: str | None, right: str | None) -> bool:
    short, long = sorted((normalize_text_for_fingerprint(left), normalize_text_for_fingerprint(right)), key=len)
    # Ignore the final possibly truncated word, but require a substantial exact passage.
    prefix = short.rsplit(" ", 1)[0]
    return len(prefix) >= 200 and len(prefix.split()) >= 30 and long.startswith(prefix + " ")


# Слова и обрывки, которые в заголовке вакансии никогда не описывают роль:
# предлоги перед местом, почтовый индекс и короткие аббревиатуры филиалов
# ("FM Rostock", "HH Billstedt"). Отбрасывать их безопасно, потому что склейку
# вакансий из разных городов всё равно запрещает проверка географии.
_LOCATION_FILLER_TOKENS: frozenset[str] = frozenset({
    "in", "im", "bei", "am", "an", "raum", "region", "standort", "umgebung", "umland",
})


def _role_tokens(
    item: NormalizedVacancyRecord | CanonicalVacancySnapshot,
) -> tuple[str, ...]:
    """Токены заголовка без города и почтового индекса.

    Источники дописывают место прямо в название: "Postbote für Pakete und Briefe
    (m/w/d) in 18059 Rostock" против "Postbote für Pakete und Briefe (m/w/d)" —
    одна и та же работа, но сходство заголовков падает до 0.62 при пороге 0.82,
    и вакансия показывается дважды. Место живёт в своём поле, и сравнивать роли
    нужно без него; если города при этом окажутся разными, склейку всё равно
    запретит проверка географии.
    """
    location = item.normalized_location
    return _role_tokens_cached(tuple(item.title_tokens), location.city, location.raw_text, location.postal_code)


@lru_cache(maxsize=16384)
def _role_tokens_cached(
    title_tokens: tuple[str, ...],
    city: str | None,
    raw_text: str | None,
    postal_code: str | None,
) -> tuple[str, ...]:
    drop = {
        token
        for source in (canonical_city(city), canonical_city(raw_text), postal_code)
        if source
        for token in source.split()
    }
    return tuple(
        token
        for token in title_tokens
        if token not in drop
        and token not in _LOCATION_FILLER_TOKENS
        and not token.isdigit()
        and len(token) > 2
    )


# Хвосты в названии фирмы, которые обозначают филиал, а не другую компанию.
_COMPANY_BRANCH_TOKENS: frozenset[str] = frozenset({
    "deutschland", "germany", "nord", "sud", "ost", "west",
    "nordost", "nordwest", "sudost", "sudwest", "mitte",
    "betrieb", "filiale", "niederlassung", "standort", "region", "zentrale",
})


def _company_core(normalized_company: str | None) -> str:
    """Название фирмы без хвоста филиала.

    Источники пишут одну и ту же фирму по-разному: "Randstad" и "Randstad
    Deutschland", "Akzent Personaldienstleistungen Nord" и то же самое плюс
    "Rostock". Раньше это были разные работодатели, и одна вакансия
    разъезжалась на несколько карточек с разными баллами.
    """
    if not normalized_company:
        return ""
    tokens = normalized_company.split()
    while len(tokens) > 1:
        tail = tokens[-1]
        if tail in _COMPANY_BRANCH_TOKENS or is_known_place(tail):
            tokens.pop()
            continue
        break
    return " ".join(tokens)


def _companies_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_core, right_core = _company_core(left), _company_core(right)
    return bool(left_core and right_core and left_core == right_core)


def _looks_like_agency(*names: str | None) -> bool:
    """Название похоже на кадровое агентство: его короткое имя склеивать только по тексту."""
    patterns = get_relevance_config().staffing_agency_company_patterns
    return any(
        re.search(pattern, normalize_text_for_fingerprint(name)) for name in names if name for pattern in patterns
    )


def _employers_match(left: str | None, right: str | None) -> bool:
    """Один работодатель по каноническому ключу (алиасы и юр. формы из конфига)."""
    left_key, right_key = employer_key(left), employer_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    # «aveato» и «aveato aveato catering berlin»: полное имя филиала начинается
    # с имени компании. Короткие ключи так не сравниваются — слишком много совпадений.
    shorter, longer = sorted((left_key.split(), right_key.split()), key=len)
    return len(" ".join(shorter)) >= 4 and longer[: len(shorter)] == shorter


def _bodies_are_the_same_posting(
    record: NormalizedVacancyRecord,
    candidate: CanonicalVacancySnapshot,
    body_similarity: float,
) -> bool:
    config = get_relevance_config()
    if body_similarity < config.duplicate_body_similarity:
        return False
    min_chars = config.duplicate_body_min_chars
    return len(record.body_text or "") >= min_chars and len(candidate.body_text or "") >= min_chars


def _title_places_differ(
    record: NormalizedVacancyRecord,
    candidate: CanonicalVacancySnapshot,
) -> bool:
    difference = set(record.title_tokens) ^ set(candidate.title_tokens)
    # Город, который просто повторяет локацию («… in 18059 Rostock»), различием не считается.
    location_words = _location_words(record.normalized_location.city, record.normalized_location.raw_text) | (
        _location_words(candidate.normalized_location.city, candidate.normalized_location.raw_text)
    )
    return any(token not in location_words and _is_place_token(token) for token in difference)


@lru_cache(maxsize=8192)
def _location_words(city: str | None, raw_text: str | None) -> frozenset[str]:
    return frozenset(word for text in (city, raw_text) for word in normalize_text_for_fingerprint(text).split())


@lru_cache(maxsize=8192)
def _is_place_token(token: str) -> bool:
    if len(token) < 4 or token.isdigit():
        return False
    city = canonical_city(token)
    return bool(city) and is_known_place(city)


def _titles_differ_only_by_role_words(
    record: NormalizedVacancyRecord,
    candidate: CanonicalVacancySnapshot,
) -> bool:
    left, right = set(record.title_tokens), set(candidate.title_tokens)
    difference = left ^ right
    if not difference or not (left & right):
        return False
    return all(classify_vacancy_de(token) is not RoleFamily.GENERIC for token in difference)


def _locations_conflict(
    record: NormalizedVacancyRecord,
    candidate: CanonicalVacancySnapshot,
) -> bool:
    """Оба города известны и это разные города.

    Неизвестный город конфликтом не считается: отсутствие данных не должно
    мешать склейке одной и той же вакансии из источника, который локацию не
    отдаёт.
    """
    left_cities = _location_cities(record.normalized_location, record.normalized_company)
    right_cities = _location_cities(candidate.normalized_location, candidate.normalized_company)
    return bool(left_cities and right_cities and left_cities.isdisjoint(right_cities))


def _locations_match(
    record: NormalizedVacancyRecord,
    candidate: CanonicalVacancySnapshot,
) -> bool:
    left_text = record.normalized_location.normalized_text
    right_text = candidate.normalized_location.normalized_text
    if left_text and right_text and left_text == right_text:
        return True

    # Районы города — это город. Без этого "Rostock" из BA и "Evershagen" из
    # Adzuna считались разными местами, дедупликация не срабатывала, и одна
    # вакансия показывалась дважды с расхождением в баллах до 30.
    return bool(
        _location_cities(record.normalized_location, record.normalized_company)
        & _location_cities(candidate.normalized_location, candidate.normalized_company)
    )


@lru_cache(maxsize=8192)
def _body_tokens(body: str | None) -> tuple[str, ...]:
    """Токены текста объявления; кэш — сравнение идёт для каждой пары записей."""
    return fingerprint_tokens(body)


def _location_cities(location: NormalizedLocation, company: str | None = None) -> frozenset[str]:
    # Филиал далеко от неточного места источника — работа там, а не в указанном городе.
    conflict = branch_location_conflict(
        company, city=location.city, postal_code=location.postal_code, location_text=location.raw_text
    )
    if conflict is not None:
        return frozenset({canonical_city(conflict[0])})
    return _cities_of(location.city, location.raw_text)


@lru_cache(maxsize=8192)
def _cities_of(city_name: str | None, raw_text: str | None) -> frozenset[str]:
    """Города, которые называет локация: распознанный город и части исходной строки.

    Adzuna пишет «Mitte, Berlin», и распознанный город — неоднозначное «Mitte»;
    без исходной строки та же вакансия DHL не совпадала с BA-записью «Berlin».
    """
    city = canonical_city(city_name)
    parts = [part for part in re.split(r"[,;/]", raw_text or "") if part.strip()]
    if city and is_known_place(city) and len(parts) <= 1:
        return frozenset({city})
    # «Britz, Berlin»: район, совпадающий с названием другого города, плюс сам
    # город. Учитываются все части, иначе «Britz» конфликтовал с «Berlin».
    cities = {city} if city else set()
    for part in parts:
        # Только точное имя населённого пункта: «Brandenburg» (земля) не должно
        # превращаться в «Brandenburg an der Havel».
        candidate = canonical_city(part)
        if candidate and candidate == normalize_text_for_fingerprint(part) and is_known_place(candidate):
            cities.add(candidate)
    return frozenset(name for name in cities if name not in _NON_CITY_LOCATION_PARTS)


_NON_CITY_LOCATION_PARTS = frozenset({"deutschland", "germany", "de"})


def _dates_are_close(left: date | None, right: date | None) -> bool:
    if left is None or right is None:
        return False
    return abs((left - right).days) <= 7


def _candidate_rank(candidate: DuplicateCandidate) -> tuple[int, float, float, int, int]:
    return (
        int("ba_employer_reference_match" in candidate.reason_codes),
        candidate.title_similarity,
        candidate.content_similarity,
        int(candidate.company_match) + int(candidate.location_match),
        int(candidate.posting_date_close),
    )
