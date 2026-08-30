from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.services.source_adapters.models import RawPayload

SourceRecordAction = Literal["create", "update", "refresh_last_seen"]
CanonicalAction = Literal["create", "update", "merge", "keep"]
MergeAction = Literal["create", "merge", "merge_existing"]


@dataclass(frozen=True, slots=True)
class NormalizedLocation:
    raw_text: str | None
    normalized_text: str | None
    country_code: str | None = None
    city: str | None = None
    postal_code: str | None = None
    tokens: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LanguageSignals:
    strong_german_required: bool = False
    german_mentioned: bool = False
    english_mentioned: bool = False
    english_required: bool = False
    english_preferred: bool = False
    low_language_signal: bool = False
    shift_signal: bool = False
    helper_role_signal: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedVacancyRecord:
    source_id: str
    source_name: str
    external_id: str
    source_reference: str | None
    source_url: str | None
    raw_payload: RawPayload
    original_title: str
    original_company: str | None
    original_location: str | None
    original_posted_at: str | None
    posted_date: date | None
    normalized_title: str
    normalized_company: str | None
    normalized_location: NormalizedLocation
    body_text: str | None
    normalized_body_text: str
    title_tokens: tuple[str, ...]
    content_tokens: tuple[str, ...]
    title_fingerprint: str
    content_fingerprint: str
    language_signals: LanguageSignals

    @property
    def source_record_key(self) -> str:
        return f"{self.source_id}:{self.external_id}"


@dataclass(frozen=True, slots=True)
class SourceRecordSnapshot:
    source_id: str
    external_id: str
    content_fingerprint: str
    source_url: str | None = None
    canonical_key: str | None = None

    @property
    def source_record_key(self) -> str:
        return f"{self.source_id}:{self.external_id}"


@dataclass(frozen=True, slots=True)
class CanonicalVacancySnapshot:
    canonical_key: str
    normalized_title: str
    normalized_company: str | None
    normalized_location: NormalizedLocation
    title_tokens: tuple[str, ...]
    content_tokens: tuple[str, ...]
    posted_date: date | None = None


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    canonical_key: str
    is_duplicate: bool
    title_similarity: float
    content_similarity: float
    company_match: bool
    location_match: bool
    posting_date_close: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceMergeDecision:
    source_record_key: str
    canonical_key: str
    action: MergeAction
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalVacancyGroup:
    canonical_key: str
    normalized_title: str
    company_name: str | None
    location_text: str | None
    country_code: str | None
    city: str | None
    posted_date: date | None
    language_signals: LanguageSignals
    source_records: tuple[NormalizedVacancyRecord, ...]
    provenance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MergeBatchResult:
    canonical_groups: tuple[CanonicalVacancyGroup, ...]
    merge_decisions: tuple[SourceMergeDecision, ...]


@dataclass(frozen=True, slots=True)
class CachePolicyDecision:
    source_record_key: str
    is_first_seen: bool
    meaningful_change: bool
    same_source_unchanged: bool
    refresh_last_seen_only: bool
    canonical_update_required: bool
    cross_source_duplicate_candidate: bool
    should_merge_into_existing: bool
    source_record_action: SourceRecordAction
    canonical_action: CanonicalAction
    matched_canonical_key: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreviewPipelineResult:
    normalized_records: tuple[NormalizedVacancyRecord, ...]
    canonical_groups: tuple[CanonicalVacancyGroup, ...]
    cache_decisions: tuple[CachePolicyDecision, ...]
