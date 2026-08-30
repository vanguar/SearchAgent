from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.services.deduper import VacancyDeduper
from app.services.hashers import combine_hash_parts
from app.services.normalization_models import (
    CanonicalVacancyGroup,
    CanonicalVacancySnapshot,
    LanguageSignals,
    MergeBatchResult,
    NormalizedLocation,
    NormalizedVacancyRecord,
    SourceMergeDecision,
)


class SourceMergeService:
    """Merge duplicate candidates into canonical vacancy groups with provenance."""

    def __init__(self, *, deduper: VacancyDeduper | None = None) -> None:
        self.deduper = deduper or VacancyDeduper()

    def merge_records(
        self,
        records: tuple[NormalizedVacancyRecord, ...],
        *,
        existing_canonicals: tuple[CanonicalVacancySnapshot, ...] = (),
    ) -> MergeBatchResult:
        groups_by_key: dict[str, _MutableCanonicalGroup] = {
            snapshot.canonical_key: _MutableCanonicalGroup.from_snapshot(snapshot)
            for snapshot in existing_canonicals
        }
        merge_decisions: list[SourceMergeDecision] = []

        for record in records:
            candidate_snapshots = tuple(group.to_snapshot() for group in groups_by_key.values())
            duplicate_candidate = self.deduper.find_duplicate_candidate(record, candidate_snapshots)

            if duplicate_candidate is None:
                canonical_key = _build_canonical_key(record)
                groups_by_key[canonical_key] = _MutableCanonicalGroup.from_record(record, canonical_key=canonical_key)
                merge_decisions.append(
                    SourceMergeDecision(
                        source_record_key=record.source_record_key,
                        canonical_key=canonical_key,
                        action="create",
                        reason_codes=("new_canonical",),
                    )
                )
                continue

            target_group = groups_by_key[duplicate_candidate.canonical_key]
            action = "merge_existing" if target_group.is_seeded and not target_group.source_records else "merge"
            target_group.add_record(record)
            merge_decisions.append(
                SourceMergeDecision(
                    source_record_key=record.source_record_key,
                    canonical_key=duplicate_candidate.canonical_key,
                    action=action,
                    reason_codes=duplicate_candidate.reason_codes,
                )
            )

        canonical_groups = tuple(
            group.to_canonical_group()
            for group in groups_by_key.values()
            if group.source_records
        )
        return MergeBatchResult(
            canonical_groups=canonical_groups,
            merge_decisions=tuple(merge_decisions),
        )


def _build_canonical_key(record: NormalizedVacancyRecord) -> str:
    return combine_hash_parts(
        record.normalized_title,
        record.normalized_company,
        record.normalized_location.normalized_text,
    )


@dataclass(slots=True)
class _MutableCanonicalGroup:
    canonical_key: str
    normalized_title: str
    company_name: str | None
    location_text: str | None
    country_code: str | None
    city: str | None
    posted_date: date | None
    language_signals: LanguageSignals
    source_records: list[NormalizedVacancyRecord] = field(default_factory=list)
    provenance: set[str] = field(default_factory=set)
    is_seeded: bool = False
    title_tokens: tuple[str, ...] = ()
    content_tokens: tuple[str, ...] = ()

    @classmethod
    def from_snapshot(cls, snapshot: CanonicalVacancySnapshot) -> _MutableCanonicalGroup:
        return cls(
            canonical_key=snapshot.canonical_key,
            normalized_title=snapshot.normalized_title,
            company_name=snapshot.normalized_company,
            location_text=snapshot.normalized_location.normalized_text,
            country_code=snapshot.normalized_location.country_code,
            city=snapshot.normalized_location.city,
            posted_date=snapshot.posted_date,
            language_signals=LanguageSignals(),
            is_seeded=True,
            title_tokens=snapshot.title_tokens,
            content_tokens=snapshot.content_tokens,
        )

    @classmethod
    def from_record(cls, record: NormalizedVacancyRecord, *, canonical_key: str) -> _MutableCanonicalGroup:
        group = cls(
            canonical_key=canonical_key,
            normalized_title=record.normalized_title,
            company_name=record.normalized_company,
            location_text=record.normalized_location.normalized_text,
            country_code=record.normalized_location.country_code,
            city=record.normalized_location.city,
            posted_date=record.posted_date,
            language_signals=record.language_signals,
            title_tokens=record.title_tokens,
            content_tokens=record.content_tokens,
        )
        group.add_record(record)
        return group

    def add_record(self, record: NormalizedVacancyRecord) -> None:
        self.source_records.append(record)
        self.provenance.add(record.source_record_key)
        self.normalized_title = _pick_canonical_text(self.normalized_title, record.normalized_title)
        self.company_name = _pick_canonical_text(self.company_name, record.normalized_company)
        self.location_text = _pick_canonical_text(self.location_text, record.normalized_location.normalized_text)
        self.country_code = self.country_code or record.normalized_location.country_code
        self.city = self.city or record.normalized_location.city
        self.posted_date = _pick_earliest_date(self.posted_date, record.posted_date)
        self.language_signals = _merge_language_signals(self.language_signals, record.language_signals)
        self.title_tokens = _pick_longer_tokens(self.title_tokens, record.title_tokens)
        self.content_tokens = _pick_longer_tokens(self.content_tokens, record.content_tokens)

    def to_snapshot(self) -> CanonicalVacancySnapshot:
        location = self.source_records[0].normalized_location if self.source_records else NormalizedLocation(
            raw_text=self.location_text,
            normalized_text=self.location_text,
            country_code=self.country_code,
            city=self.city,
        )
        return CanonicalVacancySnapshot(
            canonical_key=self.canonical_key,
            normalized_title=self.normalized_title,
            normalized_company=self.company_name,
            normalized_location=location,
            title_tokens=self.title_tokens,
            content_tokens=self.content_tokens,
            posted_date=self.posted_date,
        )

    def to_canonical_group(self) -> CanonicalVacancyGroup:
        ordered_records = tuple(
            sorted(
                self.source_records,
                key=lambda record: (record.source_id, record.external_id, record.content_fingerprint),
            )
        )
        return CanonicalVacancyGroup(
            canonical_key=self.canonical_key,
            normalized_title=self.normalized_title,
            company_name=self.company_name,
            location_text=self.location_text,
            country_code=self.country_code,
            city=self.city,
            posted_date=self.posted_date,
            language_signals=self.language_signals,
            source_records=ordered_records,
            provenance=tuple(sorted(self.provenance)),
        )


def _pick_canonical_text(current: str | None, candidate: str | None) -> str | None:
    if not current:
        return candidate
    return current


def _pick_earliest_date(current: date | None, candidate: date | None) -> date | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return current if current <= candidate else candidate


def _merge_language_signals(left: LanguageSignals, right: LanguageSignals) -> LanguageSignals:
    return LanguageSignals(
        strong_german_required=left.strong_german_required or right.strong_german_required,
        german_mentioned=left.german_mentioned or right.german_mentioned,
        english_mentioned=left.english_mentioned or right.english_mentioned,
        english_required=left.english_required or right.english_required,
        english_preferred=left.english_preferred or right.english_preferred,
        low_language_signal=left.low_language_signal or right.low_language_signal,
        shift_signal=left.shift_signal or right.shift_signal,
        helper_role_signal=left.helper_role_signal or right.helper_role_signal,
    )


def _pick_longer_tokens(current: tuple[str, ...], candidate: tuple[str, ...]) -> tuple[str, ...]:
    if len(candidate) > len(current):
        return candidate
    return current
