from app.services.normalizer import VacancyNormalizer
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview


def test_vacancy_normalizer_normalizes_fields_and_extracts_signals() -> None:
    normalizer = VacancyNormalizer()
    record = SourceRecordPreview(
        source_id="ba",
        source_name="BA (Bundesagentur fur Arbeit)",
        external_id="10000-1-S",
        source_reference="10000-1-S",
        title="Lagermitarbeiter/in (m/w/d)",
        company="Logistik Nord GmbH",
        location="10115 Berlin, Deutschland",
        posted_at="2026-04-15",
        detail_url="https://example.org/jobs/10000-1-S",
        raw_payload={
            "stellenbeschreibung": "Gute Deutschkenntnisse erforderlich. 3 Schicht im Lager.",
        },
    )

    normalized = normalizer.normalize_source_record(record)

    assert normalized.normalized_title == "lagermitarbeiter"
    assert normalized.normalized_company == "logistik nord"
    assert normalized.normalized_location.normalized_text == "berlin, DE"
    assert normalized.posted_date.isoformat() == "2026-04-15"
    assert normalized.language_signals.strong_german_required is True
    assert normalized.language_signals.shift_signal is True
    assert normalized.content_fingerprint


def test_vacancy_normalizer_handles_adapter_response_contract() -> None:
    normalizer = VacancyNormalizer()
    response = AdapterSearchResponse(
        source_id="ba",
        source_name="BA (Bundesagentur fur Arbeit)",
        records=(
            SourceRecordPreview(
                source_id="ba",
                source_name="BA (Bundesagentur fur Arbeit)",
                external_id="10000-1-S",
                source_reference="10000-1-S",
                title="Produktionshelfer/in",
                company="Werkteam Hamburg KG",
                location="20095 Hamburg, Deutschland",
                posted_at="2026-04-14",
                detail_url=None,
                raw_payload={},
            ),
        ),
        total_count=1,
        page=1,
        page_size=5,
        raw_payload={"stellenangebote": []},
    )

    normalized_records = normalizer.normalize_adapter_response(response)

    assert len(normalized_records) == 1
    assert normalized_records[0].normalized_title == "produktionshelfer"
    assert normalized_records[0].normalized_location.city == "Hamburg"
