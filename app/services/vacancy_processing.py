from __future__ import annotations

from app.services.cache_policy import VacancyCachePolicy
from app.services.normalization_models import (
    CanonicalVacancySnapshot,
    PreviewPipelineResult,
    SourceRecordSnapshot,
)
from app.services.normalizer import VacancyNormalizer
from app.services.source_adapters.models import AdapterSearchResponse
from app.services.source_merge import SourceMergeService


class VacancyProcessingService:
    """PHASE 6 deterministic pipeline: normalize, dedup, merge, and decide updates."""

    def __init__(
        self,
        *,
        normalizer: VacancyNormalizer | None = None,
        cache_policy: VacancyCachePolicy | None = None,
        source_merge_service: SourceMergeService | None = None,
    ) -> None:
        self.normalizer = normalizer or VacancyNormalizer()
        self.cache_policy = cache_policy or VacancyCachePolicy()
        self.source_merge_service = source_merge_service or SourceMergeService()

    def process_adapter_response(
        self,
        response: AdapterSearchResponse,
        *,
        existing_source_records: tuple[SourceRecordSnapshot, ...] = (),
        existing_canonicals: tuple[CanonicalVacancySnapshot, ...] = (),
    ) -> PreviewPipelineResult:
        normalized_records = self.normalizer.normalize_adapter_response(response)
        merge_result = self.source_merge_service.merge_records(
            normalized_records,
            existing_canonicals=existing_canonicals,
        )

        snapshot_lookup = {
            snapshot.source_record_key: snapshot
            for snapshot in existing_source_records
        }
        cache_decisions = []
        for record, merge_decision in zip(normalized_records, merge_result.merge_decisions, strict=True):
            existing_snapshot = snapshot_lookup.get(record.source_record_key)
            decision = self.cache_policy.evaluate(
                record,
                existing_source_record=existing_snapshot,
                merge_decision=merge_decision,
            )
            cache_decisions.append(decision)
            snapshot_lookup[record.source_record_key] = SourceRecordSnapshot(
                source_id=record.source_id,
                external_id=record.external_id,
                content_fingerprint=record.content_fingerprint,
                source_url=record.source_url,
                canonical_key=merge_decision.canonical_key,
            )

        return PreviewPipelineResult(
            normalized_records=normalized_records,
            canonical_groups=merge_result.canonical_groups,
            cache_decisions=tuple(cache_decisions),
        )
