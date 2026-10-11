"""Кадровые агентства: распознавание и исключение из выдачи.

Агентство (Arbeitsvermittlung, Zeitarbeit / Arbeitnehmerüberlassung,
Personaldienstleister) публикует чужие вакансии десятками под шаблонным
текстом. По просьбе пользователя такие вакансии скрываются целиком; это
настройка (RELEVANCE_EXCLUDE_STAFFING_AGENCIES), а не зашитое правило.

Признаки берутся из конфига, кода под конкретные фирмы нет:
* название компании («… Arbeitsvermittlung GmbH», «… Zeitarbeit», «…
  Personalservice»);
* либо несколько разных признаков в ИСХОДНОМ тексте объявления
  (Arbeitnehmerüberlassung, Personaldienstleister, «für einen Kunden»,
  Vermittlungsgutschein …). Исходный — потому что шаблонные абзацы к моменту
  анализа уже вырезаны, а признак агентства живёт именно в них.

Если агентством признана хотя бы одна вакансия работодателя, признаются и
остальные его вакансии этого прогона: вырезка Careerjet часто не доходит до
подписи агентства.
"""
from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from datetime import date
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.employer_identity import employer_key
from app.services.geo_distance import is_known_place
from app.services.hashers import normalize_text_for_fingerprint
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.search_models import SearchResultItem, VacancySignalSnapshot
from app.services.signal_negation import is_negated_signal, normalize_signal_text


@lru_cache(maxsize=4)
def _compiled(
    config: RelevanceConfig,
) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    return (
        tuple(
            re.compile(pattern)
            for pattern in (*config.staffing_agency_company_patterns, *config.staffing_agency_name_patterns)
        ),
        tuple(re.compile(pattern) for pattern in config.staffing_agency_body_patterns),
        tuple(re.compile(pattern) for pattern in config.staffing_agency_body_strong_patterns),
    )


def staffing_agency_evidence(
    canonical: CanonicalVacancyGroup,
    *,
    config: RelevanceConfig | None = None,
) -> str | None:
    """Чем вакансия выдаёт агентство; None — признаков нет."""
    resolved = config or get_relevance_config()
    company_patterns, body_patterns, strong_patterns = _compiled(resolved)
    companies = {canonical.company_name or ""} | {
        record.original_company or record.normalized_company or "" for record in canonical.source_records
    }
    # sorted: из множества имя берётся в случайном порядке, и подпись менялась от прогона к прогону.
    for company in sorted(companies):
        normalized = normalize_text_for_fingerprint(company)
        if normalized and any(pattern.search(normalized) for pattern in company_patterns):
            return f"компания «{company}»"

    text = normalize_signal_text(" ".join(_original_text(record) for record in canonical.source_records))
    for pattern in strong_patterns:
        strong = next((match for match in pattern.finditer(text) if not is_negated_signal(text, match)), None)
        if strong is not None:
            return f"признак в тексте объявления ({strong.group()})"
    matched = [
        pattern for pattern in body_patterns
        if any(not is_negated_signal(text, match) for match in pattern.finditer(text))
    ]
    if len(matched) >= max(1, resolved.staffing_agency_body_min_markers):
        return "признаки в тексте объявления (" + ", ".join(
            match.group() for pattern in matched[:2] if (match := pattern.search(text))
        ) + ")"
    return None


def mark_staffing_agencies(
    groups: Sequence[CanonicalVacancyGroup],
    signals: Sequence[VacancySignalSnapshot],
) -> list[VacancySignalSnapshot]:
    """Сигналы с признаком агентства, распространённым на всех вакансий работодателя."""
    evidence = [staffing_agency_evidence(group) for group in groups]
    agency_employers = {
        key: found
        for group, found in zip(groups, evidence, strict=True)
        if found and (key := _group_employer_key(group))
    }
    marked: list[VacancySignalSnapshot] = []
    for group, own, snapshot in zip(groups, evidence, signals, strict=True):
        reason = own or agency_employers.get(_group_employer_key(group))
        marked.append(dataclasses.replace(snapshot, staffing_agency=reason) if reason else snapshot)
    return marked


def _group_employer_key(group: CanonicalVacancyGroup) -> str:
    for record in group.source_records:
        key = employer_key(record.original_company or record.normalized_company)
        if key:
            return key
    return employer_key(group.company_name)


def _original_text(record: object) -> str:
    payload = getattr(record, "raw_payload", None)
    if isinstance(payload, dict):
        description = payload.get("description")
        if isinstance(description, str) and description:
            return description
    return getattr(record, "body_text", None) or ""


def demote_staffing_agencies(
    items: Sequence[SearchResultItem],
    *,
    config: RelevanceConfig | None = None,
) -> tuple[tuple[SearchResultItem, ...], tuple[SearchResultItem, ...]]:
    """Вакансии агентств — в самый конец выдачи, по одной на агентство в каждом городе.

    Возвращает (упорядоченные карточки, отброшенные вакансии агентств). Из
    видимых вакансий одного агентства в одном городе поиска остаётся самая
    свежая (при равной дате — с большим баллом); она идёт последней в «на проверку». Остальные уходят в
    скрытые с понятной причиной. Отклонённые карточки не трогаются.
    """
    if (config or get_relevance_config()).staffing_agency_policy != "demote":
        return tuple(items), ()
    agency_items = [item for item in items if item.bucket in ("hot", "maybe") and item.signals.staffing_agency]
    if not agency_items:
        return tuple(items), ()

    best_by_agency: dict[tuple[str, str], SearchResultItem] = {}
    for item in agency_items:
        key = _item_agency_key(item)
        current = best_by_agency.get(key)
        if current is None or _freshness_rank(item) > _freshness_rank(current):
            best_by_agency[key] = item
    kept = sorted(best_by_agency.values(), key=_freshness_rank, reverse=True)
    kept_keys = {item.canonical_group.canonical_key for item in kept}
    dropped = tuple(item for item in agency_items if item.canonical_group.canonical_key not in kept_keys)

    agency_keys = {item.canonical_group.canonical_key for item in agency_items}
    regular = [item for item in items if item.canonical_group.canonical_key not in agency_keys]
    demoted = [dataclasses.replace(item, bucket="maybe", relevance_band="medium") for item in kept]
    ordered = (
        *(item for item in regular if item.bucket == "hot"),
        *(item for item in regular if item.bucket == "maybe"),
        *demoted,
        *(item for item in regular if item.bucket not in ("hot", "maybe")),
    )
    return ordered, dropped


def _item_agency_key(item: SearchResultItem) -> tuple[str, str]:
    """Агентство и город поиска: от агентства одна вакансия в каждом городе.

    Город — тот же, что делит выдачу на разделы: иначе Randstad, оставленный в
    Нойбранденбурге, пропадал из берлинского и ростокского разделов целиком.
    """
    key = _group_employer_key(item.canonical_group)
    city = item.signals.matched_search_city or item.search_city or ""
    return (agency_brand_key(key) if key else item.canonical_group.canonical_key, city)


# Слова, с которых в названии агентства начинается филиал, где бы они ни стояли.
_AGENCY_BRANCH_MARKERS = frozenset({"nl", "niederlassung", "filiale", "standort", "zweigstelle"})
# Регион или город: филиал, только если до конца названия идут одни места.
_AGENCY_REGION_TOKENS = frozenset({"deutschland", "germany", "nord", "sud", "ost", "west", "mitte", "o"})


def agency_brand_key(employer: str) -> str:
    """Ключ агентства без филиала: «Randstad Deutschland» и «Randstad» — одно агентство.

    Правило «от агентства одна вакансия» без этого распадалось по филиалам:
    «persona service … Berlin Wedding» и «persona service … Rostock» шли как
    два агентства. Город внутри названия («Franke + Pahl Fachkräfte»: Pahl —
    тоже деревня) филиалом не считается. Только для группировки агентств —
    дедупликацию не трогает.
    """
    for pattern, brand in get_relevance_config().staffing_agency_brand_aliases:
        if re.search(pattern, employer):
            return brand
    tokens = employer.split()
    for index in range(1, len(tokens)):
        if tokens[index] in _AGENCY_BRANCH_MARKERS or all(_is_region_token(token) for token in tokens[index:]):
            return " ".join(tokens[:index])
    return employer


def _is_region_token(token: str) -> bool:
    return token in _AGENCY_REGION_TOKENS or (len(token) > 3 and is_known_place(token))


def _freshness_rank(item: SearchResultItem) -> tuple[date, int]:
    return (item.canonical_group.posted_date or date.min, item.score_result.score)
