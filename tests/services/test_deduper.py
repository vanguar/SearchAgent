from app.services.deduper import VacancyDeduper
from app.services.normalization_models import CanonicalVacancySnapshot
from app.services.normalizer import VacancyNormalizer
from app.services.source_adapters.models import SourceRecordPreview


def _make_record(
    *,
    source_name: str,
    external_id: str,
    title: str,
    company: str,
    location: str,
    posted_at: str,
    body_text: str,
):
    normalizer = VacancyNormalizer()
    return normalizer.normalize_source_record(
        SourceRecordPreview(
            source_id=source_name.casefold(),
            source_name=source_name,
            external_id=external_id,
            source_reference=external_id,
            title=title,
            company=company,
            location=location,
            posted_at=posted_at,
            detail_url=None,
            raw_payload={"description": body_text},
        )
    )


def test_vacancy_deduper_detects_cross_source_duplicate_candidate() -> None:
    deduper = VacancyDeduper()
    record = _make_record(
        source_name="BA",
        external_id="ba-1",
        title="Lagermitarbeiter/in (m/w/d)",
        company="Logistik Nord GmbH",
        location="10115 Berlin, Deutschland",
        posted_at="2026-04-15",
        body_text="Kommissionieren, Verpacken, 3 Schicht im Lager.",
    )
    candidate = CanonicalVacancySnapshot(
        canonical_key="canonical-1",
        normalized_title="lagermitarbeiter",
        normalized_company="logistik nord",
        normalized_location=record.normalized_location,
        title_tokens=record.title_tokens,
        content_tokens=record.content_tokens,
        posted_date=record.posted_date,
    )

    duplicate = deduper.find_duplicate_candidate(record, (candidate,))

    assert duplicate is not None
    assert duplicate.is_duplicate is True
    assert duplicate.company_match is True
    assert duplicate.location_match is True


def test_vacancy_deduper_rejects_unrelated_vacancy() -> None:
    deduper = VacancyDeduper()
    record = _make_record(
        source_name="BA",
        external_id="ba-1",
        title="Lagermitarbeiter/in",
        company="Logistik Nord GmbH",
        location="10115 Berlin, Deutschland",
        posted_at="2026-04-15",
        body_text="Verpacken und Lagerarbeit.",
    )
    unrelated = _make_record(
        source_name="Careerjet",
        external_id="cj-2",
        title="Buchhalter/in",
        company="Finance Team AG",
        location="80331 München, Deutschland",
        posted_at="2026-04-16",
        body_text="Abschlussarbeiten und Rechnungswesen.",
    )
    candidate = CanonicalVacancySnapshot(
        canonical_key="canonical-2",
        normalized_title=unrelated.normalized_title,
        normalized_company=unrelated.normalized_company,
        normalized_location=unrelated.normalized_location,
        title_tokens=unrelated.title_tokens,
        content_tokens=unrelated.content_tokens,
        posted_date=unrelated.posted_date,
    )

    duplicate = deduper.find_duplicate_candidate(record, (candidate,))

    assert duplicate is None
