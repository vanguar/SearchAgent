from unittest.mock import MagicMock

from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.rule_catalog import inspect_vacancy
from app.services.search_models import SearchProfileContext
from app.services.source_adapters.models import SourceRecordPreview
from app.services.summary_service import SummaryService
from app.services.translation_service import TranslationService


def _build_profile() -> SearchProfileContext:
    return SearchProfileContext(
        profile_label="Основной поиск",
        profile_source="saved",
        note_ru="Используется сохраненный профиль поиска.",
        legal_status="Section 24",
        work_authorized=True,
        german_level="basic",
        desired_roles=("склад", "логистика", "упаковка", "производство"),
        relocation_ready=True,
        shift_ok=True,
        physical_work_ok=True,
    )


def _build_canonical(*, title: str, body: str) -> CanonicalVacancyGroup:
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id="ba",
            source_name="BA",
            external_id="fixture-1",
            source_reference="fixture-1",
            title=title,
            company="Nord Team GmbH",
            location="Berlin, Deutschland",
            posted_at="2026-04-16",
            detail_url="https://example.org/jobs/fixture-1",
            raw_payload={"description": body},
        )
    )
    return CanonicalVacancyGroup(
        canonical_key="canonical-fixture",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=(record.source_record_key,),
    )


def test_translation_service_falls_back_without_live_llm() -> None:
    service = TranslationService()

    translated = service.translate_title(normalized_title="lagermitarbeiter", original_title="Lagermitarbeiter/in")

    assert translated == "Сотрудник склада"


def test_summary_service_builds_short_russian_summary_without_live_llm() -> None:
    profile = _build_profile()
    canonical = _build_canonical(
        title="Verpacker/in",
        body="Ohne Deutsch. 3 Schicht. Ab sofort.",
    )
    signals = inspect_vacancy(canonical, profile)
    summary_service = SummaryService(translation_service=TranslationService())

    summary = summary_service.build_summary(canonical, signals)

    assert summary == "Упаковщик. Языковой барьер невысокий, есть смены."


def test_translation_service_uses_llm_helper_when_configured() -> None:
    """TranslationService использует LLM-хелпер если он задан и возвращает перевод."""
    mock_helper = MagicMock()
    # Строка, которой нет в локальном словаре — значит точно от хелпера
    mock_helper.translate_title.return_value = "Оператор-комплектовщик"
    service = TranslationService(helper=mock_helper)

    result = service.translate_title(
        normalized_title="senior lagermitarbeiter",
        original_title="Senior Lagermitarbeiter/in",
    )

    mock_helper.translate_title.assert_called_once()
    assert result == "Оператор-комплектовщик"


def test_translation_service_falls_back_when_llm_returns_none() -> None:
    """Если LLM-хелпер вернул None, TranslationService использует локальный словарь."""
    mock_helper = MagicMock()
    mock_helper.translate_title.return_value = None
    service = TranslationService(helper=mock_helper)

    result = service.translate_title(
        normalized_title="lagermitarbeiter",
        original_title="Lagermitarbeiter/in",
    )

    assert result == "Сотрудник склада"  # локальный словарь


def test_summary_service_uses_llm_helper_when_configured() -> None:
    """SummaryService использует LLM-хелпер если он задан и возвращает резюме."""
    profile = _build_profile()
    canonical = _build_canonical(title="Verpacker/in", body="Lagerhalle Hamburg. Keine Vorkenntnisse.")
    signals = inspect_vacancy(canonical, profile)

    mock_helper = MagicMock()
    mock_helper.summarize.return_value = "Упаковщик в Гамбурге, без опыта и немецкого."
    service = SummaryService(translation_service=TranslationService(), helper=mock_helper)

    summary = service.build_summary(canonical, signals)

    mock_helper.summarize.assert_called_once()
    assert summary == "Упаковщик в Гамбурге, без опыта и немецкого."


def test_summary_service_falls_back_when_llm_returns_none() -> None:
    """Если LLM-хелпер вернул None, SummaryService строит резюме детерминированно."""
    profile = _build_profile()
    canonical = _build_canonical(title="Verpacker/in", body="Ohne Deutsch. 3 Schicht. Ab sofort.")
    signals = inspect_vacancy(canonical, profile)

    mock_helper = MagicMock()
    mock_helper.summarize.return_value = None
    service = SummaryService(translation_service=TranslationService(), helper=mock_helper)

    summary = service.build_summary(canonical, signals)

    assert summary == "Упаковщик. Языковой барьер невысокий, есть смены."


def test_german_language_it_posting_without_requirement_does_not_claim_german_required() -> None:
    profile = _build_profile()
    canonical = _build_canonical(
        title="Python Backend Developer",
        body="Wir suchen Entwickler fur Backend APIs. Die Anzeige ist auf Deutsch geschrieben.",
    )
    signals = inspect_vacancy(canonical, profile)

    summary = SummaryService(translation_service=TranslationService()).build_summary(canonical, signals)

    assert summary is not None
    assert "нужен хороший немецкий" not in summary
    assert "требуется" not in summary.lower()


def test_posting_without_shift_signal_does_not_claim_shift_work() -> None:
    profile = _build_profile()
    canonical = _build_canonical(
        title="Python Backend Developer",
        body="Backend APIs mit Python und FastAPI. Flexible Arbeitszeiten.",
    )
    signals = inspect_vacancy(canonical, profile)

    summary = SummaryService(translation_service=TranslationService()).build_summary(canonical, signals)

    assert summary is not None
    assert "смен" not in summary.lower()


def test_deutsch_c1_erforderlich_summary_can_claim_german_required() -> None:
    profile = _build_profile()
    canonical = _build_canonical(
        title="Python Backend Developer",
        body="Backend APIs mit Python. Deutsch C1 erforderlich.",
    )
    signals = inspect_vacancy(canonical, profile)

    summary = SummaryService(translation_service=TranslationService()).build_summary(canonical, signals)

    assert summary is not None
    assert "нужен хороший немецкий" in summary.lower()


def test_deutschkenntnisse_von_vorteil_summary_is_not_requirement() -> None:
    profile = _build_profile()
    canonical = _build_canonical(
        title="Python Backend Developer",
        body="Python APIs. Deutschkenntnisse von Vorteil, Englisch im Team moglich.",
    )
    signals = inspect_vacancy(canonical, profile)

    summary = SummaryService(translation_service=TranslationService()).build_summary(canonical, signals)

    assert summary is not None
    assert "нужен хороший немецкий" not in summary
    assert "требуется" not in summary.lower()
