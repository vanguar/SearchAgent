from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord

FilterDecision = Literal["allow", "review", "reject"]
SearchBucket = Literal["hot", "maybe", "rejected"]
RelevanceBand = Literal["high", "medium", "low"]
ProfileSource = Literal["saved", "fallback", "degraded"]
SourceStatusKind = Literal["success", "warning", "error", "disabled"]

_LOW_BARRIER_PROFILE_HINTS = (
    "lager",
    "warehouse",
    "logistik",
    "verpack",
    "pack",
    "produktion",
    "helper",
    "helfer",
    "склад",
    "логист",
    "упаков",
    "производ",
    "помощ",
)
_LOW_GERMAN_HINTS = {
    "none",
    "no",
    "kein",
    "keine",
    "нет",
    "basic",
    "elementary",
    "anfanger",
    "beginner",
    "a1",
    "a2",
    "начальный",
    "базовый",
    "слабый",
}
_PROFILE_SPACES_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class RuleHit:
    code: str
    label_ru: str
    weight: int = 0


@dataclass(frozen=True, slots=True)
class SearchProfileContext:
    profile_label: str
    profile_source: ProfileSource
    note_ru: str | None = None
    legal_status: str | None = None
    work_authorized: bool = True
    german_level: str | None = None
    english_level: str | None = None
    desired_roles: tuple[str, ...] = ()
    excluded_roles: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    relocation_ready: bool | None = None
    shift_ok: bool | None = None
    physical_work_ok: bool | None = None
    housing_needed: bool | None = None
    start_availability_text: str | None = None
    no_german_required: bool = False
    section24_interpreted: bool = False
    search_query_terms: tuple[str, ...] = ()
    driver_license: str | None = None

    @classmethod
    def fallback(cls, *, note_ru: str) -> SearchProfileContext:
        return cls(
            profile_label="Базовый профиль поиска",
            profile_source="fallback",
            note_ru=note_ru,
            work_authorized=True,
            german_level=None,
            desired_roles=("склад", "логистика", "упаковка", "производство"),
            preferred_locations=(),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
            housing_needed=False,
        )

    @classmethod
    def degraded(cls, *, note_ru: str) -> SearchProfileContext:
        return cls(
            profile_label="Временный локальный профиль",
            profile_source="degraded",
            note_ru=note_ru,
            work_authorized=True,
            german_level=None,
            desired_roles=("склад", "логистика", "упаковка", "производство"),
            preferred_locations=(),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
            housing_needed=False,
        )

    @property
    def low_german(self) -> bool:
        return is_low_german_level(self.german_level)

    @property
    def accepts_shifts(self) -> bool:
        return self.shift_ok is not False

    @property
    def allows_relocation(self) -> bool:
        return self.relocation_ready is not False

    @property
    def low_barrier_focus(self) -> bool:
        if not self.desired_roles:
            return True
        normalized_roles = " ".join(normalize_profile_text(role) for role in self.desired_roles)
        return any(hint in normalized_roles for hint in _LOW_BARRIER_PROFILE_HINTS)


@dataclass(frozen=True, slots=True)
class VacancySignalSnapshot:
    combined_text: str
    positive_role_hits: tuple[RuleHit, ...] = ()
    negative_role_hits: tuple[RuleHit, ...] = ()
    desired_role_hits: tuple[RuleHit, ...] = ()
    excluded_role_hits: tuple[RuleHit, ...] = ()
    location_match: bool | None = None
    location_hits: tuple[str, ...] = ()
    strong_german_required: bool = False
    german_any_required: bool = False
    low_language_signal: bool = False
    shift_signal: bool = False
    relocation_signal: bool = False
    immediate_start_signal: bool = False
    degree_required: bool = False
    vocational_training_required: bool = False
    strong_experience_required: bool = False
    entry_level_signal: bool = False
    sponsorship_ambiguity: bool = False
    required_driver_license_categories: tuple[str, ...] = ()
    allowed_driver_license_categories: tuple[str, ...] = ()
    optional_driver_license_categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilterResult:
    decision: FilterDecision
    positive_hits: tuple[RuleHit, ...] = ()
    rejection_hits: tuple[RuleHit, ...] = ()
    review_hits: tuple[RuleHit, ...] = ()

    @property
    def hard_reject(self) -> bool:
        return self.decision == "reject"

    @property
    def review_required(self) -> bool:
        return self.decision == "review"


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int
    positive_hits: tuple[RuleHit, ...] = ()
    negative_hits: tuple[RuleHit, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchSourceState:
    source_id: str
    source_name: str
    status_label: str
    # "success" = adapter ran and returned records
    # "warning" = adapter ran but returned 0 records
    # "error"   = adapter raised an exception
    # "disabled" = adapter is off in config (not currently used at runtime, for UI pre-search)
    status_kind: Literal["success", "warning", "error", "disabled"] = "success"
    raw_count: int = 0
    total_count: int | None = None
    normalized_count: int = 0
    canonical_count: int = 0
    warnings: tuple[str, ...] = ()
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    canonical_group: CanonicalVacancyGroup
    primary_record: NormalizedVacancyRecord
    signals: VacancySignalSnapshot
    filter_result: FilterResult
    score_result: ScoreResult
    bucket: SearchBucket
    explanation_ru: str
    summary_ru: str | None = None
    translated_title_ru: str | None = None
    relevance_band: RelevanceBand = "high"
    # Deterministic role family derived from canonical normalized title — used for feedback matching.
    role_family: str | None = None
    search_query: str | None = None

@dataclass(frozen=True, slots=True)
class DedupPreviewItem:
    canonical_key: str
    title: str
    company_name: str | None = None
    location_text: str | None = None
    source_name: str | None = None
    source_count: int = 1
    original_url: str | None = None


@dataclass(frozen=True, slots=True)
class HiddenFilteredItem:
    canonical_key: str
    title: str
    company_name: str | None = None
    location_text: str | None = None
    source_name: str | None = None
    original_url: str | None = None
    rejection_reasons: tuple[RuleHit, ...] = ()

@dataclass(frozen=True, slots=True)
class SearchAttemptRecord:
    """Single attempt in a progressive fallback search run."""
    attempt_number: int          # 0-based sequential index across all stages
    primary_query: str           # original query before any fallback expansion
    stage_name: str
    query_used: str
    language_relaxation_applied: bool
    raw_count: int
    normalized_count: int
    deduped_count: int
    hot_count: int
    review_count: int            # "maybe" bucket count
    rejected_count: int
    non_rejected_count: int      # hot + review, kept for stop-condition use
    reason_continued: str | None  # None = final or sufficient attempt
    rejection_reason_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class SearchAttemptSummary:
    """Full summary of all attempts in an orchestrated search run."""
    primary_query: str
    final_query_used: str
    fallback_used: bool
    language_relaxation_used: bool
    attempts: tuple[SearchAttemptRecord, ...]
    user_message_ru: str | None  # None = primary was sufficient, no explanation needed


@dataclass(frozen=True, slots=True)
class SearchQueryResultGroup:
    query: str
    hot_results: tuple[SearchResultItem, ...] = ()
    maybe_results: tuple[SearchResultItem, ...] = ()

    @property
    def visible_count(self) -> int:
        return len(self.hot_results) + len(self.maybe_results)


@dataclass(frozen=True, slots=True)
class SearchRunResult:
    profile: SearchProfileContext
    source_states: tuple[SearchSourceState, ...]
    results: tuple[SearchResultItem, ...]
    hot_results: tuple[SearchResultItem, ...] = ()
    maybe_results: tuple[SearchResultItem, ...] = ()
    rejected_results: tuple[SearchResultItem, ...] = ()
    deduped_preview_items: tuple[DedupPreviewItem, ...] = ()
    hidden_filtered_items: tuple[HiddenFilteredItem, ...] = ()
    total_raw_records: int = 0
    total_normalized_records: int = 0
    total_canonical_results: int = 0
    attempt_summary: SearchAttemptSummary | None = None
    query_result_groups: tuple[SearchQueryResultGroup, ...] = ()

    @property
    def has_results(self) -> bool:
        return bool(self.results)


def normalize_profile_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text.casefold().replace("ё", "е"))
    cleaned = "".join(character for character in normalized if not unicodedata.combining(character))
    return _PROFILE_SPACES_RE.sub(" ", cleaned).strip()


def is_low_german_level(value: str | None) -> bool:
    normalized_value = normalize_profile_text(value)
    if not normalized_value:
        return False
    return normalized_value in _LOW_GERMAN_HINTS
