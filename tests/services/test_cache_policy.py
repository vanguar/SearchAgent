from app.services.cache_policy import VacancyCachePolicy
from app.services.normalization_models import SourceMergeDecision, SourceRecordSnapshot
from app.services.normalizer import VacancyNormalizer
from app.services.source_adapters.models import SourceRecordPreview


def _make_record(
    *,
    source_id: str = "ba",
    source_name: str = "BA",
    external_id: str,
    body_text: str,
):
    normalizer = VacancyNormalizer()
    return normalizer.normalize_source_record(
        SourceRecordPreview(
            source_id=source_id,
            source_name=source_name,
            external_id=external_id,
            source_reference=external_id,
            title="Lagermitarbeiter/in",
            company="Logistik Nord GmbH",
            location="10115 Berlin, Deutschland",
            posted_at="2026-04-15",
            detail_url="https://example.org/jobs/" + external_id,
            raw_payload={"description": body_text},
        )
    )


def test_cache_policy_marks_first_seen_record_as_create() -> None:
    policy = VacancyCachePolicy()
    record = _make_record(external_id="ba-1", body_text="Kommissionieren im Lager.")

    decision = policy.evaluate(
        record,
        existing_source_record=None,
        merge_decision=SourceMergeDecision(
            source_record_key=record.source_record_key,
            canonical_key="canonical-1",
            action="create",
            reason_codes=("new_canonical",),
        ),
    )

    assert decision.is_first_seen is True
    assert decision.source_record_action == "create"
    assert decision.canonical_action == "create"


def test_cache_policy_uses_source_id_for_same_source_identity() -> None:
    policy = VacancyCachePolicy()
    record = _make_record(
        source_name="BA (Bundesagentur fur Arbeit)",
        external_id="ba-1",
        body_text="Kommissionieren und Verpacken im Lager.",
    )
    existing = SourceRecordSnapshot(
        source_id="ba",
        external_id="ba-1",
        content_fingerprint=record.content_fingerprint,
        source_url=record.source_url,
        canonical_key="canonical-1",
    )

    decision = policy.evaluate(
        record,
        existing_source_record=existing,
        merge_decision=SourceMergeDecision(
            source_record_key=record.source_record_key,
            canonical_key="canonical-1",
            action="merge_existing",
            reason_codes=("company_match", "location_match"),
        ),
    )

    assert record.source_record_key == "ba:ba-1"
    assert existing.source_record_key == "ba:ba-1"
    assert decision.same_source_unchanged is True
    assert decision.refresh_last_seen_only is True
    assert decision.source_record_action == "refresh_last_seen"
    assert decision.canonical_action == "keep"


def test_cache_policy_marks_meaningful_change_and_merge() -> None:
    policy = VacancyCachePolicy()
    old_record = _make_record(external_id="ba-1", body_text="Kommissionieren im Lager.")
    new_record = _make_record(external_id="ba-1", body_text="Kommissionieren und Arbeit in 3 Schicht.")
    existing = SourceRecordSnapshot(
        source_id="ba",
        external_id="ba-1",
        content_fingerprint=old_record.content_fingerprint,
        source_url=old_record.source_url,
        canonical_key="canonical-1",
    )

    decision = policy.evaluate(
        new_record,
        existing_source_record=existing,
        merge_decision=SourceMergeDecision(
            source_record_key=new_record.source_record_key,
            canonical_key="canonical-1",
            action="merge_existing",
            reason_codes=("content_match",),
        ),
    )

    assert decision.meaningful_change is True
    assert decision.source_record_action == "update"
    assert decision.canonical_action == "merge"
    assert decision.canonical_update_required is True
