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


def _snapshot_of(record, *, key: str = "canonical-x"):
    return CanonicalVacancySnapshot(
        canonical_key=key,
        normalized_title=record.normalized_title,
        normalized_company=record.normalized_company,
        normalized_location=record.normalized_location,
        title_tokens=record.title_tokens,
        content_tokens=record.content_tokens,
        posted_date=record.posted_date,
    )


_BOILERPLATE = (
    "Wir suchen zum naechstmoeglichen Zeitpunkt Verstaerkung. "
    "Kommissionieren, Verpacken und Versand im Schichtbetrieb. "
    "Wir bieten uebertarifliche Bezahlung und Urlaubsgeld."
)


def test_city_district_and_its_city_are_the_same_location() -> None:
    """Biestow — район Ростока.

    Раньше BA отдавал "Rostock", Adzuna — "Biestow", записи не склеивались,
    и одна вакансия показывалась дважды с расхождением в баллах до 30.
    """
    deduper = VacancyDeduper()
    left = _make_record(
        source_name="BA", external_id="ba-1", title="Staplerfahrer (m/w/d)",
        company="Jobtimum GmbH", location="18055 Rostock, Deutschland",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )
    right = _make_record(
        source_name="Adzuna", external_id="adz-1", title="Staplerfahrer (m/w/d)",
        company="Jobtimum", location="Biestow",
        posted_at="2026-09-02", body_text=_BOILERPLATE,
    )

    verdict = deduper.evaluate(left, _snapshot_of(right))

    assert verdict.location_match is True
    assert verdict.is_duplicate is True


def test_branch_suffix_does_not_make_a_different_employer() -> None:
    deduper = VacancyDeduper()
    left = _make_record(
        source_name="BA", external_id="ba-2", title="Versandmitarbeiter (m/w/d)",
        company="Randstad", location="18055 Rostock, Deutschland",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )
    right = _make_record(
        source_name="Careerjet", external_id="cj-2", title="Versandmitarbeiter (m/w/d)",
        company="Randstad Deutschland", location="Rostock",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )

    verdict = deduper.evaluate(left, _snapshot_of(right))

    assert verdict.company_match is True
    assert verdict.is_duplicate is True


def test_same_agency_in_two_cities_stays_two_vacancies() -> None:
    """Тело объявления кадрового агентства шаблонно и совпадает между филиалами.

    Без запрета по географии "Versandmitarbeiter" от Randstad в Ростоке и в
    Муггенстурме (700 км) склеивались в одну карточку, и одна из двух реальных
    вакансий исчезала из выдачи.
    """
    deduper = VacancyDeduper()
    left = _make_record(
        source_name="BA", external_id="ba-3", title="Versandmitarbeiter (m/w/d)",
        company="Randstad Deutschland", location="18055 Rostock, Deutschland",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )
    right = _make_record(
        source_name="BA", external_id="ba-4", title="Versandmitarbeiter (m/w/d)",
        company="Randstad Deutschland", location="76461 Muggensturm, Deutschland",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )

    verdict = deduper.evaluate(left, _snapshot_of(right))

    assert verdict.is_duplicate is False


def test_different_employers_sharing_a_first_word_are_not_merged() -> None:
    deduper = VacancyDeduper()
    left = _make_record(
        source_name="BA", external_id="ba-5", title="Fahrer (m/w/d)",
        company="Meyer Transport GmbH", location="Rostock",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )
    right = _make_record(
        source_name="BA", external_id="ba-6", title="Fahrer (m/w/d)",
        company="Meyer Bau GmbH", location="Rostock",
        posted_at="2026-09-01", body_text=_BOILERPLATE,
    )

    verdict = deduper.evaluate(left, _snapshot_of(right))

    assert verdict.company_match is False
    assert verdict.is_duplicate is False
