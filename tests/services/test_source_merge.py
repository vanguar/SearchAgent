from app.services.normalizer import VacancyNormalizer
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService, _pick_canonical_text


def _make_record(
    *,
    source_id: str | None = None,
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
            source_id=source_id or source_name.casefold(),
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


def test_source_merge_service_merges_safe_duplicate_candidates() -> None:
    service = SourceMergeService()
    first = _make_record(
        source_id="ba",
        source_name="BA (Bundesagentur fur Arbeit)",
        external_id="ba-1",
        title="Lagermitarbeiter/in",
        company="Logistik Nord GmbH",
        location="10115 Berlin, Deutschland",
        posted_at="2026-04-15",
        body_text="Kommissionieren und Verpacken im Lager.",
    )
    second = _make_record(
        source_id="careerjet",
        source_name="Careerjet",
        external_id="cj-1",
        title="Lagermitarbeiter (m/w/d)",
        company="Logistik Nord",
        location="Berlin, Germany",
        posted_at="2026-04-16",
        body_text="Verpacken und Kommissionieren im Lager.",
    )

    result = service.merge_records((first, second))

    assert len(result.canonical_groups) == 1
    assert len(result.canonical_groups[0].provenance) == 2
    assert result.canonical_groups[0].provenance == ("ba:ba-1", "careerjet:cj-1")
    assert result.merge_decisions[0].action == "create"
    assert result.merge_decisions[1].action == "merge"


def test_source_merge_service_keeps_non_duplicates_separate() -> None:
    service = SourceMergeService()
    first = _make_record(
        source_name="BA",
        external_id="ba-1",
        title="Lagermitarbeiter/in",
        company="Logistik Nord GmbH",
        location="10115 Berlin, Deutschland",
        posted_at="2026-04-15",
        body_text="Kommissionieren und Verpacken im Lager.",
    )
    second = _make_record(
        source_name="Careerjet",
        external_id="cj-1",
        title="Produktionsplaner/in",
        company="Werkteam Hamburg KG",
        location="20095 Hamburg, Deutschland",
        posted_at="2026-04-16",
        body_text="Planung der Produktionslinien.",
    )

    result = service.merge_records((first, second))

    assert len(result.canonical_groups) == 2
    assert all(decision.action == "create" for decision in result.merge_decisions)


def test_pick_canonical_text_keeps_first_non_empty_value() -> None:
    assert _pick_canonical_text("lagermitarbeiter", "lagerhelfer") == "lagermitarbeiter"
    assert _pick_canonical_text("lagermitarbeiter", None) == "lagermitarbeiter"
    assert _pick_canonical_text(None, "lagerhelfer") == "lagerhelfer"
