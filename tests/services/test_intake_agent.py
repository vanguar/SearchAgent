from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.intake_agent import IntakeAgentService
from app.services.llm_client import LLMStatus, _set_runtime_status


IT_TIMEOUT_TEXT = """
Я в Германии по §24 AufenthG, разрешение на работу есть.
Ищу Middle Python Developer / Backend Developer роли: FastAPI, Django, AI Automation,
Telegram Bot Developer, scraping/parser developer, LLM integrations.
Предпочтительные регионы: Berlin, Hamburg, Rostock, вся Германия. Remote/hybrid ок.
Готов к переезду: да. Немецкий A1, английский working proficiency.
Не интересны Senior-only, Lead, Principal, CTO, Fluent English, .ru/.by, Russia/Belarus,
склад, производство и ручная физическая работа.
"""

REMOTE_WORLDWIDE_PROFILE_TEXT = """
Я живу в Tribsees, Germany. Нахожусь в Германии по §24, имею право работать.
Ищу только удалённую работу: remote, part-time, contract или freelance.
К офису, гибриду и переезду не готов, так как рассматриваю именно удалённый формат.
Поиск не ограничивается Германией, рассматриваю вакансии по всему миру:
Germany, EU, UK, USA, Canada, international remote companies.
Основные роли: Middle Python Developer, Python Backend Developer, FastAPI Developer,
Django Developer, Backend Developer, AI Automation Engineer, LLM Integration Developer,
Telegram Bot Developer, Scraping/Parser Developer, Automation Developer, MVP Developer.
Немецкий A2. Английский A1. Физическая работа: нет.
Права: есть водительские права категории B. Машина: личного авто в Германии нет.
"""


EXAMPLE_TEXT = (
    "Я в Германии, по 24 параграфу, немецкого почти не знаю, английский слабый, "
    "можно по всей Германии, готов к переезду, смены ок."
)

# Followup answers supply what conservative parser can't extract without LLM
EXAMPLE_FOLLOWUP = {
    "desired_roles": "Склад, Упаковка",
    "willing_to_relocate": "да",
    "shift_ok": "да",
}


def _make_sqlite_session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    return Session(bind=engine)


def test_intake_agent_analyze_returns_missing_fields_for_partial_input() -> None:
    service = IntakeAgentService()

    analysis = service.analyze("Хочу работать в Германии, немецкого нет")

    assert "desired_roles" in analysis.missing_fields
    assert "preferred_regions" in analysis.missing_fields


def test_intake_agent_save_persists_profile_into_existing_models() -> None:
    service = IntakeAgentService()
    db = _make_sqlite_session()

    result = service.save_confirmed_profile(
        free_text=EXAMPLE_TEXT,
        followup_answers=EXAMPLE_FOLLOWUP,
        db=db,
    )

    assert result.saved is True
    assert result.user_profile_id is not None
    assert result.search_profile_id is not None

    user_profile = db.query(UserProfile).one()
    search_profile = db.query(SearchProfile).one()

    assert user_profile.legal_status == "section_24"
    assert user_profile.work_authorized is True
    assert search_profile.desired_roles is not None
    assert len(search_profile.desired_roles) > 0


def test_intake_agent_save_fails_without_roles() -> None:
    """Without LLM and without followup answers, roles are missing → save blocked."""
    service = IntakeAgentService()
    db = _make_sqlite_session()

    result = service.save_confirmed_profile(
        free_text=EXAMPLE_TEXT,
        followup_answers=None,
        db=db,
    )

    assert result.saved is False
    assert "desired_roles" in (result.message or "") or result.user_profile_id is None


def test_intake_agent_llm_timeout_uses_it_deterministic_fallback() -> None:
    class TimeoutLLM:
        def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict:
            _ = text
            _ = prompt_template
            _set_runtime_status(LLMStatus.PROVIDER_ERROR)
            return {}

    service = IntakeAgentService(llm_client=TimeoutLLM())

    analysis = service.analyze(IT_TIMEOUT_TEXT)

    assert "AI extraction failed / timed out" in " ".join(analysis.warnings)
    assert "Python Developer" in analysis.draft.desired_roles
    assert "Backend Developer" in analysis.draft.desired_roles
    assert "FastAPI Developer" in analysis.draft.desired_roles
    assert "Telegram Bot Developer" in analysis.draft.desired_roles
    assert "Warehouse" not in analysis.draft.desired_roles
    assert "Production" not in analysis.draft.desired_roles
    assert "Warehouse" in analysis.draft.excluded_roles
    assert "Production" in analysis.draft.excluded_roles
    assert "Senior-only roles" in analysis.draft.excluded_roles
    assert ".ru domains" in analysis.draft.excluded_roles
    assert ".by domains" in analysis.draft.excluded_roles
    assert analysis.missing_fields == ()


def test_remote_worldwide_profile_saves_without_deutschland_location_prefill() -> None:
    service = IntakeAgentService()
    db = _make_sqlite_session()

    result = service.save_confirmed_profile(
        free_text=IT_TIMEOUT_TEXT,
        followup_answers={
            "preferred_regions": "worldwide remote, Germany, EU, UK, USA, Canada",
            "willing_to_relocate": "нет",
        },
        db=db,
    )

    assert result.saved is True
    search_profile = db.query(SearchProfile).one()
    assert search_profile.search_location_de == "remote"
    assert "worldwide remote" in search_profile.preferred_locations


def test_remote_worldwide_profile_text_extracts_global_regions_without_llm() -> None:
    analysis = IntakeAgentService().analyze(REMOTE_WORLDWIDE_PROFILE_TEXT)

    assert "worldwide remote" in analysis.draft.preferred_regions
    assert "Deutschland" in analysis.draft.preferred_regions
    assert "EU" in analysis.draft.preferred_regions
    assert "UK" in analysis.draft.preferred_regions
    assert "USA" in analysis.draft.preferred_regions
    assert "Canada" in analysis.draft.preferred_regions
    assert analysis.draft.willing_to_relocate is False
    assert "preferred_regions" not in analysis.missing_fields
    assert "willing_to_relocate" not in analysis.missing_fields
