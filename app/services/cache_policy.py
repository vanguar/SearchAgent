from __future__ import annotations

from app.services.hashers import normalize_text_for_fingerprint
from app.services.normalization_models import (
    CachePolicyDecision,
    NormalizedVacancyRecord,
    SourceMergeDecision,
    SourceRecordSnapshot,
)


class VacancyCachePolicy:
    """Decide how source records and canonical records should be updated."""

    def evaluate(
        self,
        record: NormalizedVacancyRecord,
        *,
        existing_source_record: SourceRecordSnapshot | None,
        merge_decision: SourceMergeDecision,
    ) -> CachePolicyDecision:
        is_first_seen = existing_source_record is None
        meaningful_change = is_first_seen or self._has_meaningful_change(
            record=record,
            existing_source_record=existing_source_record,
        )
        same_source_unchanged = not is_first_seen and not meaningful_change
        refresh_last_seen_only = same_source_unchanged

        if is_first_seen:
            source_record_action = "create"
        elif same_source_unchanged:
            source_record_action = "refresh_last_seen"
        else:
            source_record_action = "update"

        if refresh_last_seen_only:
            canonical_action = "keep"
        elif merge_decision.action == "create":
            canonical_action = "create" if is_first_seen else "update"
        else:
            canonical_action = "merge"

        reason_codes = list(merge_decision.reason_codes)
        reason_codes.append("first_seen" if is_first_seen else "existing_source_record")
        if same_source_unchanged:
            reason_codes.append("same_source_unchanged")
        elif not is_first_seen:
            reason_codes.append("meaningful_change_detected")

        return CachePolicyDecision(
            source_record_key=record.source_record_key,
            is_first_seen=is_first_seen,
            meaningful_change=meaningful_change,
            same_source_unchanged=same_source_unchanged,
            refresh_last_seen_only=refresh_last_seen_only,
            canonical_update_required=not refresh_last_seen_only,
            cross_source_duplicate_candidate=merge_decision.action in {"merge", "merge_existing"},
            should_merge_into_existing=merge_decision.action in {"merge", "merge_existing"},
            source_record_action=source_record_action,
            canonical_action=canonical_action,
            matched_canonical_key=merge_decision.canonical_key,
            reason_codes=tuple(reason_codes),
        )

    def _has_meaningful_change(
        self,
        *,
        record: NormalizedVacancyRecord,
        existing_source_record: SourceRecordSnapshot,
    ) -> bool:
        if existing_source_record.content_fingerprint != record.content_fingerprint:
            return True

        current_url = normalize_text_for_fingerprint(existing_source_record.source_url)
        incoming_url = normalize_text_for_fingerprint(record.source_url)
        return current_url != incoming_url
