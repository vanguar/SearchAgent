from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.services.hashers import combine_hash_parts, fingerprint_tokens, normalize_text_for_fingerprint
from app.services.language_signal_extractor import LanguageSignalExtractor
from app.services.location_normalizer import LocationNormalizer
from app.services.normalization_models import NormalizedVacancyRecord
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview
from app.services.title_normalizer import TitleNormalizer

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(?:gmbh|mbh|ag|kg|gbr|ohg|ug|haftungsbeschrankt|co|co kg|e v|ev|se)\b"
)
_BODY_KEYS = (
    "stellenangebotsBeschreibung",
    "stellenbeschreibung",
    "description",
    "beschreibung",
    "body",
    "job_description",
)


class VacancyNormalizer:
    """Normalize raw adapter records into deterministic canonical-ready records."""

    def __init__(
        self,
        *,
        title_normalizer: TitleNormalizer | None = None,
        location_normalizer: LocationNormalizer | None = None,
        language_signal_extractor: LanguageSignalExtractor | None = None,
    ) -> None:
        self.title_normalizer = title_normalizer or TitleNormalizer()
        self.location_normalizer = location_normalizer or LocationNormalizer()
        self.language_signal_extractor = language_signal_extractor or LanguageSignalExtractor()

    def normalize_adapter_response(self, response: AdapterSearchResponse) -> tuple[NormalizedVacancyRecord, ...]:
        return tuple(self.normalize_source_record(record) for record in response.records)

    def normalize_source_record(self, record: SourceRecordPreview) -> NormalizedVacancyRecord:
        body_text = _extract_body_text(record.raw_payload)
        normalized_title = self.title_normalizer.normalize(record.title)
        normalized_company = _normalize_company_name(record.company)
        normalized_location = self.location_normalizer.normalize(record.location)
        normalized_body_text = normalize_text_for_fingerprint(body_text)
        title_tokens = fingerprint_tokens(normalized_title)
        content_source = " ".join(
            part
            for part in (
                normalized_title,
                normalized_company,
                normalized_location.normalized_text,
                normalized_body_text,
            )
            if part
        )
        content_tokens = fingerprint_tokens(content_source)
        language_signals = self.language_signal_extractor.extract(title=record.title, body_text=body_text)

        return NormalizedVacancyRecord(
            source_id=record.source_id,
            source_name=record.source_name,
            external_id=record.external_id,
            source_reference=record.source_reference,
            source_url=record.detail_url,
            raw_payload=record.raw_payload,
            original_title=record.title,
            original_company=record.company,
            original_location=record.location,
            original_posted_at=record.posted_at,
            posted_date=_parse_posted_date(record.posted_at),
            normalized_title=normalized_title,
            normalized_company=normalized_company,
            normalized_location=normalized_location,
            body_text=body_text,
            normalized_body_text=normalized_body_text,
            title_tokens=title_tokens,
            content_tokens=content_tokens,
            title_fingerprint=combine_hash_parts(normalized_title),
            content_fingerprint=combine_hash_parts(
                normalized_title,
                normalized_company,
                normalized_location.normalized_text,
                normalized_body_text,
            ),
            language_signals=language_signals,
        )


def _normalize_company_name(company_name: str | None) -> str | None:
    normalized = normalize_text_for_fingerprint(company_name)
    if not normalized:
        return None
    stripped = normalize_text_for_fingerprint(_COMPANY_SUFFIX_RE.sub(" ", normalized))
    return stripped or normalized


def _extract_body_text(raw_payload: Any) -> str | None:
    if not isinstance(raw_payload, dict):
        return None

    for key in _BODY_KEYS:
        value = raw_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for value in raw_payload.values():
        if isinstance(value, dict):
            nested = _extract_body_text(value)
            if nested:
                return nested

    return None


def _parse_posted_date(value: str | None) -> date | None:
    if not value:
        return None

    cleaned = value.strip()
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    except ValueError:
        return None
