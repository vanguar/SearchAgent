from app.services.normalization_models import CanonicalVacancySnapshot, SourceRecordSnapshot
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview
from app.services.vacancy_processing import VacancyProcessingService


def test_vacancy_processing_service_runs_phase6_pipeline_on_adapter_output() -> None:
    service = VacancyProcessingService()
    response = AdapterSearchResponse(
        source_id="ba",
        source_name="BA (Bundesagentur fur Arbeit)",
        records=(
            SourceRecordPreview(
                source_id="ba",
                source_name="BA (Bundesagentur fur Arbeit)",
                external_id="ba-1",
                source_reference="ba-1",
                title="Lagermitarbeiter/in (m/w/d)",
                company="Logistik Nord GmbH",
                location="10115 Berlin, Deutschland",
                posted_at="2026-04-15",
                detail_url="https://example.org/jobs/ba-1",
                raw_payload={"description": "Kommissionieren und Verpacken im Lager."},
            ),
        ),
        total_count=1,
        page=1,
        page_size=5,
        raw_payload={"stellenangebote": []},
    )

    result = service.process_adapter_response(response)

    assert len(result.normalized_records) == 1
    assert result.normalized_records[0].source_record_key == "ba:ba-1"
    assert result.normalized_records[0].normalized_title == "lagermitarbeiter"
    assert len(result.canonical_groups) == 1
    assert result.cache_decisions[0].source_record_action == "create"
    assert result.cache_decisions[0].canonical_action == "create"


def test_vacancy_processing_service_respects_existing_snapshots() -> None:
    service = VacancyProcessingService()
    response = AdapterSearchResponse(
        source_id="ba",
        source_name="BA (Bundesagentur fur Arbeit)",
        records=(
            SourceRecordPreview(
                source_id="ba",
                source_name="BA (Bundesagentur fur Arbeit)",
                external_id="ba-1",
                source_reference="ba-1",
                title="Lagermitarbeiter/in",
                company="Logistik Nord GmbH",
                location="10115 Berlin, Deutschland",
                posted_at="2026-04-15",
                detail_url="https://example.org/jobs/ba-1",
                raw_payload={"description": "Kommissionieren und Verpacken im Lager."},
            ),
        ),
        total_count=1,
        page=1,
        page_size=5,
        raw_payload={"stellenangebote": []},
    )
    initial_result = service.process_adapter_response(response)
    existing_source = SourceRecordSnapshot(
        source_id="ba",
        external_id="ba-1",
        content_fingerprint=initial_result.normalized_records[0].content_fingerprint,
        source_url="https://example.org/jobs/ba-1",
        canonical_key="canonical-1",
    )
    existing_canonical = CanonicalVacancySnapshot(
        canonical_key="canonical-1",
        normalized_title="lagermitarbeiter",
        normalized_company="logistik nord",
        normalized_location=initial_result.normalized_records[0].normalized_location,
        title_tokens=initial_result.normalized_records[0].title_tokens,
        content_tokens=initial_result.normalized_records[0].content_tokens,
        posted_date=initial_result.normalized_records[0].posted_date,
    )

    result = service.process_adapter_response(
        response,
        existing_source_records=(existing_source,),
        existing_canonicals=(existing_canonical,),
    )

    assert result.cache_decisions[0].refresh_last_seen_only is True
    assert result.cache_decisions[0].canonical_action == "keep"
