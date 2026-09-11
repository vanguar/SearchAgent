from unittest.mock import MagicMock

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.intake_agent import IntakeAgentService
from app.services.llm_client import LLMStatus, _set_runtime_status
from app.services.profile_parser import (
    DRIVER_B_FERNVERKEHR_ROLE,
    DRIVER_B_FERNVERKEHR_SEARCH_TERMS,
)
from app.services.search_models import SearchRunResult
from app.services.search_profile_resolver import DatabaseSearchProfileResolver
from app.services.search_service import SearchService
from app.services.source_adapters.models import SourceSearchInput
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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

DRIVER_B_CANONICAL_TEXT = """
Ищу работу в Германии водителем категории B.

Меня интересует работа на Sprinter или Transporter до 3,5 т с преимущественно длинными маршрутами.

Предпочитаю Fernverkehr (дальние перевозки), Direktfahrten (прямые рейсы),
Sonderfahrten (специальные рейсы), Expressfahrten (срочные перевозки), а также маршруты
по Германии с небольшим количеством точек загрузки и разгрузки.

Предпочтительный формат работы: загрузка груза, длительный переезд и разгрузка в одной
или нескольких точках, примерно 1–5 остановок за маршрут.

У меня водительское удостоверение категории B.

Мой уровень немецкого языка — A1.

Я готов к переезду в другой город Германии ради подходящей работы.

Я нахожусь в Германии по временной защите согласно §24 Aufenthaltsgesetz и имею право
работать в Германии.

Меня НЕ интересует классическая массовая доставка посылок с десятками или сотнями остановок
в день: Paketzustellung, Paketbote, Postzustellung, Briefzustellung, а также работа формата
Amazon/DPD/Hermes/GLS с постоянной доставкой от двери к двери.

Целевые варианты работы:

- Fahrer Klasse B
- Sprinterfahrer
- Transporterfahrer
- Fahrer bis 3,5 t
- Fahrer im Fernverkehr
- Fahrer für Direktfahrten
- Fahrer für Sonderfahrten
- Fahrer für Expressfahrten

Ищу вакансии прежде всего в Германии.
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


def _capture_saved_profile_query_plan(db: Session, *, profile_id: int) -> tuple[str, ...]:
    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    service = SearchService(
        profile_resolver=DatabaseSearchProfileResolver(session_factory=session_factory),
    )

    def empty_search_result(**kwargs) -> SearchRunResult:
        return SearchRunResult(
            profile=kwargs["profile"],
            source_states=(),
            results=(),
        )

    service.search = MagicMock(side_effect=empty_search_result)
    # Введённый запрос теперь идёт первой попыткой, поэтому чтобы увидеть именно план
    # профиля, подставляем в строку то же, что подставил бы UI — search_query_de профиля.
    saved_profile = db.get(SearchProfile, profile_id)
    prefilled_query = (saved_profile.search_query_de or "") if saved_profile is not None else ""
    service.orchestrated_search(
        search_input=SourceSearchInput(
            query=prefilled_query,
            location="Deutschland",
            page=1,
            page_size=8,
        ),
        source_ids=("ba",),
        profile_id=profile_id,
    )
    return tuple(call.kwargs["search_input"].query for call in service.search.call_args_list)


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


def test_remote_worldwide_intent_does_not_turn_into_fake_saved_location() -> None:
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
    assert search_profile.preferred_locations == ["Deutschland", "EU", "UK", "USA", "Canada"]


def test_remote_worldwide_profile_text_extracts_global_regions_without_llm() -> None:
    analysis = IntakeAgentService().analyze(REMOTE_WORLDWIDE_PROFILE_TEXT)

    assert analysis.draft.remote_allowed is True
    assert analysis.draft.international_remote_allowed is True
    assert "worldwide remote" not in analysis.draft.preferred_regions
    assert "international remote companies" not in analysis.draft.preferred_regions
    assert "Deutschland" in analysis.draft.preferred_regions
    assert "EU" in analysis.draft.preferred_regions
    assert "UK" in analysis.draft.preferred_regions
    assert "USA" in analysis.draft.preferred_regions
    assert "Canada" in analysis.draft.preferred_regions
    assert analysis.draft.willing_to_relocate is False
    assert "preferred_regions" not in analysis.missing_fields
    assert "willing_to_relocate" not in analysis.missing_fields


def test_courier_intake_keeps_geography_and_respects_remote_negation() -> None:
    text = (
        "Ищу работу курьером в Германии. Предпочтительно Rostock, Stralsund или Greifswald. "
        "Удалённая работа мне не нужна. Немецкий A1, смены подходят, переезд возможен."
    )

    analysis = IntakeAgentService().analyze(text)

    assert analysis.draft.preferred_regions == ["Rostock", "Stralsund", "Greifswald"]
    assert analysis.draft.remote_allowed is False
    assert analysis.draft.international_remote_allowed is False


def test_remote_it_intake_preserves_intent_without_fake_locations() -> None:
    analysis = IntakeAgentService().analyze(
        "Ищу работу с AI-инструментами удалённо в международных компаниях по всему миру."
    )

    assert analysis.draft.remote_allowed is True
    assert analysis.draft.international_remote_allowed is True
    assert analysis.draft.preferred_regions == []
    assert "preferred_regions" not in analysis.missing_fields


def test_intake_save_reload_keeps_preferred_locations_clean() -> None:
    class LocationPollutingLLM:
        def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict:
            _ = text
            assert "ТОЛЬКО географические места" in prompt_template
            return {
                "current_country": "Germany",
                "legal_status": "section_24",
                "work_authorization": True,
                "german_level": "basic",
                "desired_roles": ["Курьер"],
                "preferred_regions": [
                    "international remote companies",
                    "worldwide remote",
                    "Rostock",
                    "Stralsund",
                    "Greifswald",
                ],
                "remote_allowed": False,
                "international_remote_allowed": False,
                "relocation_ready": True,
                "shift_work_allowed": True,
                "physical_work_allowed": True,
                "search_query_terms": ["Kurierfahrer"],
                "evidence_by_field": {"desired_roles": "Ищу работу курьером"},
            }

        def translate_location_to_de(self, location: str) -> str:
            return location

    db = _make_sqlite_session()
    service = IntakeAgentService(llm_client=LocationPollutingLLM())
    saved = service.save_confirmed_profile(
        free_text=(
            "Ищу работу курьером в Rostock, Stralsund или Greifswald. "
            "Удалённая работа мне не нужна."
        ),
        followup_answers=None,
        db=db,
    )

    assert saved.saved is True
    assert saved.search_profile_id is not None
    db.expire_all()
    reloaded = db.get(SearchProfile, saved.search_profile_id)
    assert reloaded is not None
    assert reloaded.preferred_locations == ["Rostock", "Stralsund", "Greifswald"]


def test_driver_b_intake_llm_path_persists_specialized_search_profile() -> None:
    class DriverBLLM:
        def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict:
            assert "Driver B – Fernverkehr" in prompt_template
            return {
                "current_country": "Germany",
                "legal_status": "section_24",
                "work_authorization": True,
                "german_level": "basic",
                "english_level": "basic",
                "desired_roles": [DRIVER_B_FERNVERKEHR_ROLE],
                "excluded_roles": ["Mass parcel delivery"],
                "preferred_regions": ["Deutschland"],
                "relocation_ready": True,
                "shift_work_allowed": True,
                "driving_license": "B",
                "search_query_terms": list(DRIVER_B_FERNVERKEHR_SEARCH_TERMS),
                "evidence_by_field": {
                    "desired_roles": "Ищу работу водителем",
                    "driving_license": "Führerschein Klasse B",
                },
            }

        def translate_location_to_de(self, location: str) -> str:
            return location

    text = (
        "Ищу работу водителем с Führerschein Klasse B на Sprinter или Transporter до 3,5 т "
        "по Германии. Интересуют Fernverkehr, Direktfahrten, Sonderfahrten и Expressfahrten."
    )
    service = IntakeAgentService(llm_client=DriverBLLM())
    analysis = service.analyze(text)

    assert analysis.missing_fields == ()
    assert analysis.draft.desired_roles == [DRIVER_B_FERNVERKEHR_ROLE]
    assert analysis.draft.driving_license == "B"
    assert analysis.draft.preferred_regions == ["Deutschland"]
    assert tuple(analysis.draft.search_query_terms) == DRIVER_B_FERNVERKEHR_SEARCH_TERMS

    db = _make_sqlite_session()
    saved = service.save_confirmed_analysis(analysis=analysis, free_text=text, db=db)
    assert saved.saved is True
    profile = db.query(SearchProfile).one()
    assert profile.desired_roles == [DRIVER_B_FERNVERKEHR_ROLE]
    assert profile.driver_license == "B"
    assert profile.preferred_locations == ["Deutschland"]
    assert tuple(profile.search_query_terms or ()) == DRIVER_B_FERNVERKEHR_SEARCH_TERMS
    assert profile.search_query_de == DRIVER_B_FERNVERKEHR_SEARCH_TERMS[0]


def _assert_canonical_driver_b_analysis(analysis) -> None:
    assert analysis.draft.desired_roles == [DRIVER_B_FERNVERKEHR_ROLE]
    assert tuple(analysis.draft.search_query_terms) == DRIVER_B_FERNVERKEHR_SEARCH_TERMS
    assert analysis.draft.driving_license == "B"
    assert analysis.draft.german_level == "basic"
    assert analysis.draft.willing_to_relocate is True
    assert analysis.draft.legal_status == "section_24"
    assert analysis.draft.work_authorized is True
    assert analysis.draft.preferred_regions == ["Deutschland"]
    assert any("parcel" in role.lower() or "paket" in role.lower() for role in analysis.draft.excluded_roles)
    assert analysis.missing_fields == ("shift_ok",)
    assert [question.field_name for question in analysis.questions] == ["shift_ok"]


def test_canonical_driver_b_profile_extracts_all_explicit_fields_without_llm() -> None:
    analysis = IntakeAgentService().analyze(DRIVER_B_CANONICAL_TEXT)

    _assert_canonical_driver_b_analysis(analysis)


def test_canonical_driver_b_profile_has_no_questions_after_shift_followup() -> None:
    analysis = IntakeAgentService().analyze(
        DRIVER_B_CANONICAL_TEXT,
        followup_answers={"shift_ok": "да"},
    )

    assert analysis.draft.shift_ok is True
    assert analysis.missing_fields == ()
    assert analysis.questions == ()


def test_canonical_driver_b_profile_llm_path_survives_post_processing_and_saves() -> None:
    class CanonicalDriverBLLM:
        def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict:
            assert text == DRIVER_B_CANONICAL_TEXT
            assert "ищу работу в Германии водителем" in prompt_template
            return {
                "current_country": "Germany",
                "legal_status": "section_24",
                "work_authorization": True,
                "german_level": "A1",
                "desired_roles": [DRIVER_B_FERNVERKEHR_ROLE],
                "excluded_roles": [
                    "Paketzustellung",
                    "Paketbote",
                    "Postzustellung",
                    "Briefzustellung",
                ],
                "preferred_regions": ["Deutschland"],
                "relocation_ready": True,
                "driving_license": "B",
                "search_query_terms": list(DRIVER_B_FERNVERKEHR_SEARCH_TERMS),
                "evidence_by_field": {
                    "desired_roles": "Ищу работу в Германии водителем категории B",
                    "driving_license": "водительское удостоверение категории B",
                },
            }

        def translate_location_to_de(self, location: str) -> str:
            return location

    service = IntakeAgentService(llm_client=CanonicalDriverBLLM())
    analysis = service.analyze(DRIVER_B_CANONICAL_TEXT)
    _assert_canonical_driver_b_analysis(analysis)

    db = _make_sqlite_session()
    saved = service.save_confirmed_profile(
        free_text=DRIVER_B_CANONICAL_TEXT,
        followup_answers={"shift_ok": "да"},
        db=db,
    )

    assert saved.saved is True
    user_profile = db.query(UserProfile).one()
    search_profile = db.query(SearchProfile).one()
    assert user_profile.legal_status == "section_24"
    assert user_profile.work_authorized is True
    assert user_profile.german_level == "basic"
    assert search_profile.desired_roles == [DRIVER_B_FERNVERKEHR_ROLE]
    assert search_profile.driver_license == "B"
    assert search_profile.relocation_ready is True
    assert tuple(search_profile.search_query_terms or ()) == DRIVER_B_FERNVERKEHR_SEARCH_TERMS
    assert search_profile.search_query_de == DRIVER_B_FERNVERKEHR_SEARCH_TERMS[0]


def test_noncanonical_driver_b_llm_terms_are_saved_and_planned_as_canonical_13() -> None:
    noncanonical_llm_terms = (
        "Fahrer Klasse B",
        "Sprinterfahrer",
        "Transporterfahrer",
        "Fahrer bis 3,5 t",
        "Fahrer im Fernverkehr",
        "Fahrer für Direktfahrten",
        "Fahrer für Sonderfahrten",
        "Fahrer für Expressfahrten",
        "Planensprinter Fahrer",
        "Koffersprinter Fahrer",
    )

    class NoncanonicalDriverBLLM:
        def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict:
            assert text == DRIVER_B_CANONICAL_TEXT
            assert "Driver B – Fernverkehr" in prompt_template
            return {
                "current_country": "Germany",
                "legal_status": "section_24",
                "work_authorization": True,
                "german_level": "A1",
                "desired_roles": list(noncanonical_llm_terms),
                "excluded_roles": [
                    "Paketzustellung",
                    "Paketbote",
                    "Postzustellung",
                    "Briefzustellung",
                ],
                "preferred_regions": ["Deutschland"],
                "relocation_ready": True,
                "driving_license": "B",
                "search_query_terms": list(noncanonical_llm_terms),
                "evidence_by_field": {
                    "desired_roles": "Ищу работу в Германии водителем категории B",
                    "driving_license": "водительское удостоверение категории B",
                },
            }

        def translate_location_to_de(self, location: str) -> str:
            return location

    db = _make_sqlite_session()
    saved = IntakeAgentService(llm_client=NoncanonicalDriverBLLM()).save_confirmed_profile(
        free_text=DRIVER_B_CANONICAL_TEXT,
        followup_answers={"shift_ok": "да"},
        db=db,
    )

    assert saved.saved is True
    assert saved.search_profile_id is not None
    search_profile = db.get(SearchProfile, saved.search_profile_id)
    assert search_profile is not None
    assert search_profile.desired_roles == [DRIVER_B_FERNVERKEHR_ROLE]
    assert tuple(search_profile.search_query_terms or ()) == DRIVER_B_FERNVERKEHR_SEARCH_TERMS
    assert search_profile.search_query_de == DRIVER_B_FERNVERKEHR_SEARCH_TERMS[0]

    query_plan = _capture_saved_profile_query_plan(db, profile_id=saved.search_profile_id)
    assert query_plan == DRIVER_B_FERNVERKEHR_SEARCH_TERMS
    assert not {"fahrer", "kurier", "zusteller", "lieferfahrer"}.intersection(query_plan)
