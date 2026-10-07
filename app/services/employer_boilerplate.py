"""Шаблонный текст работодателя вырезается до анализа требований.

Агентство пишет в каждой вакансии одни и те же абзацы: про сертификат AZAV,
DEKRA, Vermittlungsgutschein, согласие на обработку данных, «wir freuen uns
auf Sie». Эти абзацы ничего не говорят о конкретной работе, но в них бывают
слова, по которым движок ставит признаки — «Grundkenntnisse», «ohne
Vorkenntnisse», «Quereinsteiger». В итоге шаблон агентства давал каждой его
вакансии одинаковые бонусы, и «Fahrer – Event- und Messebau» получал 99.

Абзац считается шаблонным, если повторяется у одного работодателя в большей
части его вакансий этого прогона, или если в нём есть служебный маркер из
конфига. Дедупликация к этому моменту уже прошла, поэтому одна вакансия из
нескольких источников считается один раз.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.employer_identity import employer_key
from app.services.hashers import normalize_text_for_fingerprint
from app.services.language_signal_extractor import LanguageSignalExtractor
from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord
from app.services.source_merge import merge_language_signals

_LANGUAGE_EXTRACTOR = LanguageSignalExtractor()

# Границы абзацев и предложений. Careerjet склеивает абзацы тремя пробелами.
_SEGMENT_SPLIT_RE = re.compile(r"(?:\s{3,}|\n+|(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9]))")


@lru_cache(maxsize=4)
def _marker_patterns(config: RelevanceConfig) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in config.boilerplate_marker_patterns)


@lru_cache(maxsize=4)
def _protected_patterns(config: RelevanceConfig) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in config.boilerplate_protected_patterns)


def split_segments(text: str | None) -> list[str]:
    return [segment.strip() for segment in _SEGMENT_SPLIT_RE.split(text or "") if segment and segment.strip()]


def strip_employer_boilerplate(
    groups: Sequence[CanonicalVacancyGroup],
    *,
    config: RelevanceConfig | None = None,
) -> tuple[CanonicalVacancyGroup, ...]:
    """Те же группы, но в описаниях нет шаблонных абзацев работодателя."""
    resolved = config or get_relevance_config()
    markers = _marker_patterns(resolved)
    protected = _protected_patterns(resolved)

    groups_by_employer: dict[str, list[CanonicalVacancyGroup]] = {}
    for group in groups:
        key = _group_employer_key(group)
        if key:
            groups_by_employer.setdefault(key, []).append(group)

    repeated: dict[str, frozenset[str]] = {}
    for key, employer_groups in groups_by_employer.items():
        if len(employer_groups) < resolved.boilerplate_min_vacancies:
            continue
        counts: Counter[str] = Counter()
        for group in employer_groups:
            counts.update({
                normalized
                for record in group.source_records
                for segment in split_segments(record.body_text)
                if len(normalized := normalize_text_for_fingerprint(segment)) >= resolved.boilerplate_min_segment_chars
            })
        threshold = max(resolved.boilerplate_min_vacancies, resolved.boilerplate_min_share * len(employer_groups))
        repeated[key] = frozenset(segment for segment, count in counts.items() if count >= threshold)

    cleaned: list[CanonicalVacancyGroup] = []
    for group in groups:
        boilerplate = repeated.get(_group_employer_key(group), frozenset())
        records = tuple(
            _strip_record(record, boilerplate, markers, protected) for record in group.source_records
        )
        if all(new is old for new, old in zip(records, group.source_records, strict=True)):
            cleaned.append(group)
            continue
        # Языковые сигналы группы считались по исходному тексту вместе с шаблоном.
        language_signals = records[0].language_signals
        for record in records[1:]:
            language_signals = merge_language_signals(language_signals, record.language_signals)
        cleaned.append(dataclasses.replace(group, source_records=records, language_signals=language_signals))
    return tuple(cleaned)


def _group_employer_key(group: CanonicalVacancyGroup) -> str:
    for record in group.source_records:
        key = employer_key(record.original_company or record.normalized_company)
        if key:
            return key
    return employer_key(group.company_name)


def _strip_record(
    record: NormalizedVacancyRecord,
    boilerplate: frozenset[str],
    markers: tuple[re.Pattern[str], ...],
    protected: tuple[re.Pattern[str], ...],
) -> NormalizedVacancyRecord:
    segments = split_segments(record.body_text)
    if not segments:
        return record
    kept: list[str] = []
    removed = False
    for segment in segments:
        normalized = normalize_text_for_fingerprint(segment)
        if any(pattern.search(normalized) for pattern in protected):
            kept.append(segment)
            continue
        if normalized in boilerplate or any(marker.search(normalized) for marker in markers):
            removed = True
            continue
        kept.append(segment)
    if not removed:
        return record
    body = "   ".join(kept)
    return dataclasses.replace(
        record,
        body_text=body,
        normalized_body_text=normalize_text_for_fingerprint(body),
        language_signals=_LANGUAGE_EXTRACTOR.extract(title=record.original_title, body_text=body),
    )
