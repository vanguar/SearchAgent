"""End-to-end pipeline test for the IT/MVP/AI Automation profile.

Verifies the full path:
  fake LLM (extract_profile_fields_v2)
  → ProfileLLMExtractor.extract()
  → post_process()
  → ProfileValidator.validate()
  → IntakeAgentService.analyze()

No real OpenAI calls — all LLM output is provided via mock.

Also documents and tests the downstream search-query flow:
  ProfileExtractionResult.search_query_terms  — extracted but NOT yet persisted to DB
  SearchProfile.search_query_de               — the actual query sent to job boards
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.services.intake_agent import IntakeAgentService
from app.services.profile_extraction_model import ProfileExtractionResult
from app.services.profile_parser import extraction_result_to_draft
from app.services.profile_post_processor import process as post_process
from app.services.profile_role_taxonomy import RoleFamily, classify_roles
from app.services.search_normalizer import normalize_query_from_roles


# ---------------------------------------------------------------------------
# IT/MVP/AI Automation profile text (real user text)
# ---------------------------------------------------------------------------

IT_PROFILE_TEXT = """\
Я хочу создать рабочий профиль для поиска IT/MVP/AI Automation проектов и вакансий.

Моя основная роль: AI-assisted Python Developer, MVP Builder, AI Agent Developer, Automation Developer.

Я занимаюсь созданием MVP, AI-агентов, внутренних бизнес-инструментов, автоматизации, backend-систем,
API, Telegram-ботов, scraping/data pipelines и LLM-интеграций.

Основной стек: Python, FastAPI, Django, REST APIs, WebSockets, asyncio, PostgreSQL, SQLite, Redis,
OpenAI API, Anthropic Claude, Deepgram, Telegram Bot API, scraping, automation.

Мне интересны роли:
Python Developer, MVP Developer, AI Agent Developer, Automation Developer, Backend Developer,
FastAPI Developer, Django Developer, LLM Integration Developer,
Scraping / Data Extraction Developer, Telegram Bot Developer, Internal Tools Developer.

Не интересны: склад, производство, ручная физическая работа, низкоквалифицированные не-IT вакансии,
чистый frontend без backend-логики.

Страна поиска: Германия + международная дистанционная работа.
Предпочтительные регионы: Берлин, Росток, Гамбург, вся Германия. Готов работать remote из Германии.

Готовность к переезду: да.
Правовой статус: §24 AufenthG. Разрешение на работу: есть.
Немецкий: A1. Английский: working proficiency.
Готов начать: сразу.
Физическая работа: нет.
Водительские права: категории B. Личной машины нет.
"""

# ---------------------------------------------------------------------------
# Simulated LLM output (what a real LLM should return for this profile)
# ---------------------------------------------------------------------------

_LLM_RESPONSE: dict = {
    "current_country": "Germany",
    "current_city": None,  # no explicit "I live in <city>" — cities are search regions
    "legal_status": "section_24",
    "work_authorization": True,
    "german_level": "basic",
    "english_level": "intermediate",
    "native_languages": ["Ukrainian", "Russian"],
    "desired_roles": [
        "Python Developer",
        "MVP Developer",
        "AI Agent Developer",
        "Automation Developer",
        "Backend Developer",
        "FastAPI Developer",
        "Django Developer",
        "LLM Integration Developer",
        "Scraping / Data Extraction Developer",
        "Telegram Bot Developer",
        "Internal Tools Developer",
    ],
    "excluded_roles": [
        "Warehouse",
        "Production",
        "manual physical work",
        "low-skilled non-IT jobs",
        "Frontend Only",
    ],
    "desired_role_families": [
        "it_software", "ai_automation", "backend", "scraping_data", "telegram_bots",
    ],
    "excluded_role_families": ["warehouse", "production_manufacturing", "manual_labor"],
    "preferred_regions": ["Berlin", "Rostock", "Hamburg", "Deutschland"],
    "remote_allowed": True,
    "international_remote_allowed": True,
    "relocation_ready": True,
    "work_modes": ["remote", "hybrid", "office"],
    "employment_types": ["full-time", "part-time", "project-based", "freelance"],
    "shift_work_allowed": None,  # not relevant for IT
    "physical_work_allowed": False,
    "housing_needed": None,
    "availability": "immediately",
    "driving_license": "B",
    "has_car": False,
    "core_stack": [
        "Python", "FastAPI", "Django", "PostgreSQL", "Redis",
        "OpenAI API", "Anthropic Claude", "Telegram Bot API",
    ],
    "search_query_terms": [
        # German
        "Python Entwickler",
        "Backend Entwickler",
        "FastAPI Entwickler",
        "Django Entwickler",
        "KI Entwickler",
        "Automatisierung Entwickler",
        "LLM Integration",
        "API Entwickler",
        "Telegram Bot Entwickler",
        "MVP Entwickler",
        # English
        "Python Developer",
        "Backend Developer",
        "FastAPI Developer",
        "Django Developer",
        "AI Agent Developer",
        "AI Automation Developer",
        "LLM Integration Developer",
        "Automation Developer",
        "API Developer",
        "Telegram Bot Developer",
        "Scraping Developer",
        "Data Extraction Developer",
        "MVP Developer",
        "Internal Tools Developer",
    ],
    "negative_query_terms": [
        "Lagerhelfer", "Lagermitarbeiter", "Lagerist",
        "Produktionshelfer", "Produktionsmitarbeiter",
        "warehouse worker", "production worker",
        "Fahrer", "driver",
    ],
    "profile_summary": (
        "Python/AI Developer specializing in MVP, AI agents, backend systems, "
        "scraping, and LLM integrations. Based in Germany (§24), open to remote/hybrid."
    ),
    "questions_needed": [],
    "evidence_by_field": {
        "current_city": "",  # no explicit residence city
        "legal_status": "§24 AufenthG",
        "work_authorization": "Разрешение на работу: есть",
        "driving_license": "права категории B",
    },
    "confidence_by_field": {
        "desired_roles": 0.97,
        "excluded_roles": 0.95,
        "german_level": 0.95,
        "english_level": 0.90,
        "legal_status": 0.99,
        "work_authorization": 0.99,
        "relocation_ready": 0.95,
        "physical_work_allowed": 0.98,
    },
}

# ---------------------------------------------------------------------------
# Fixture: full pipeline output via fake LLM
# ---------------------------------------------------------------------------

@pytest.fixture()
def it_analysis():
    """Run IntakeAgentService.analyze() with fake LLM — no real OpenAI call."""
    mock_llm = MagicMock()
    mock_llm.extract_profile_fields_v2.return_value = _LLM_RESPONSE

    service = IntakeAgentService(llm_client=mock_llm)
    return service.analyze(IT_PROFILE_TEXT)


def test_llm_extraction_accepts_null_evidence_values():
    payload = dict(_LLM_RESPONSE)
    payload["evidence_by_field"] = {"current_city": None, "desired_roles": "Мне интересны роли"}
    mock_llm = MagicMock()
    mock_llm.extract_profile_fields_v2.return_value = payload

    analysis = IntakeAgentService(llm_client=mock_llm).analyze(IT_PROFILE_TEXT)

    assert "Python Developer" in analysis.draft.desired_roles
    assert analysis.missing_fields == ()


def test_llm_extraction_accepts_common_loose_json_shapes():
    payload = dict(_LLM_RESPONSE)
    payload.update(
        {
            "desired_roles": "Python Developer, Backend Developer, FastAPI Developer",
            "excluded_roles": "Warehouse, Production",
            "preferred_regions": "Berlin, Rostock, Hamburg, Deutschland",
            "work_authorization": "yes",
            "relocation_ready": "да",
            "physical_work_allowed": "нет",
            "has_car": "no",
            "confidence_by_field": {
                "desired_roles": "0.95",
                "preferred_regions": None,
                "bad": "not-a-number",
            },
            "evidence_by_field": {"current_city": None, "desired_roles": 123},
        }
    )
    mock_llm = MagicMock()
    mock_llm.extract_profile_fields_v2.return_value = payload

    analysis = IntakeAgentService(llm_client=mock_llm).analyze(IT_PROFILE_TEXT)

    assert "Python Developer" in analysis.draft.desired_roles
    assert "Backend Developer" in analysis.draft.desired_roles
    assert "Warehouse" in analysis.draft.excluded_roles
    assert "Berlin" in analysis.draft.preferred_regions
    assert analysis.draft.work_authorized is True
    assert analysis.draft.willing_to_relocate is True
    assert analysis.draft.physical_work_ok is False
    assert analysis.draft.has_car is False
    assert analysis.missing_fields == ()


def test_remote_worldwide_markers_are_kept_even_if_llm_returns_only_countries():
    payload = dict(_LLM_RESPONSE)
    payload["preferred_regions"] = ["Germany", "EU", "UK", "USA", "Canada"]
    payload["international_remote_allowed"] = True
    mock_llm = MagicMock()
    mock_llm.extract_profile_fields_v2.return_value = payload

    analysis = IntakeAgentService(llm_client=mock_llm).analyze(
        IT_PROFILE_TEXT + "\nИщу worldwide remote и international remote companies."
    )

    assert "worldwide remote" in analysis.draft.preferred_regions
    assert "international remote companies" in analysis.draft.preferred_regions


@pytest.fixture()
def it_draft(it_analysis):
    return it_analysis.draft


@pytest.fixture()
def it_extraction():
    """Post-processed ProfileExtractionResult (before conversion to draft)."""
    raw = ProfileExtractionResult.model_validate(_LLM_RESPONSE)
    return post_process(raw, raw_text_lower=IT_PROFILE_TEXT.lower())


# ---------------------------------------------------------------------------
# 1. Desired roles
# ---------------------------------------------------------------------------

IT_DESIRED_ROLES = [
    "Python Developer",
    "MVP Developer",
    "AI Agent Developer",
    "Automation Developer",
    "Backend Developer",
    "FastAPI Developer",
    "Django Developer",
    "LLM Integration Developer",
    "Scraping / Data Extraction Developer",
    "Telegram Bot Developer",
    "Internal Tools Developer",
]

ROLES_MUST_NOT_BE_DESIRED = [
    "Склад", "Warehouse",
    "Производство", "Production",
    "Водитель", "Driver", "Delivery Driver", "Courier",
    "manual physical work",
    "low-skilled non-IT jobs",
    "pure frontend without backend logic",
]


class TestDesiredRoles:
    def test_all_it_roles_present(self, it_draft):
        for role in IT_DESIRED_ROLES:
            assert role in it_draft.desired_roles, f"Expected '{role}' in desired_roles"

    def test_physical_roles_not_desired(self, it_draft):
        desired_lower = {r.lower() for r in it_draft.desired_roles}
        for role in ROLES_MUST_NOT_BE_DESIRED:
            assert role.lower() not in desired_lower, (
                f"'{role}' must NOT be in desired_roles, but it is: {it_draft.desired_roles}"
            )

    def test_driver_not_in_desired_roles(self, it_draft):
        desired_lower = " ".join(it_draft.desired_roles).lower()
        assert "водитель" not in desired_lower
        assert "driver" not in desired_lower


# ---------------------------------------------------------------------------
# 2. Excluded roles
# ---------------------------------------------------------------------------

class TestExcludedRoles:
    def test_warehouse_in_excluded(self, it_draft):
        excluded_lower = " ".join(it_draft.excluded_roles).lower()
        assert "warehouse" in excluded_lower or "склад" in excluded_lower

    def test_production_in_excluded(self, it_draft):
        excluded_lower = " ".join(it_draft.excluded_roles).lower()
        assert "production" in excluded_lower or "производств" in excluded_lower

    def test_physical_work_in_excluded(self, it_draft):
        excluded_lower = " ".join(it_draft.excluded_roles).lower()
        assert "physical" in excluded_lower or "физическ" in excluded_lower or "warehouse" in excluded_lower

    def test_pure_frontend_in_excluded(self, it_draft):
        excluded_lower = " ".join(it_draft.excluded_roles).lower()
        assert "frontend" in excluded_lower


# ---------------------------------------------------------------------------
# 3. Role families
# ---------------------------------------------------------------------------

class TestRoleFamilies:
    def test_it_desired_families_present(self, it_extraction):
        it_fams = {
            RoleFamily.IT_SOFTWARE, RoleFamily.AI_AUTOMATION, RoleFamily.BACKEND,
            RoleFamily.SCRAPING_DATA, RoleFamily.TELEGRAM_BOTS,
        }
        desired_fam_values = set(it_extraction.desired_role_families)
        for fam in it_fams:
            assert fam.value in desired_fam_values, f"Expected {fam.value} in desired_role_families"

    def test_physical_families_not_desired(self, it_extraction):
        physical_fams = {"warehouse", "production_manufacturing", "manual_labor"}
        desired = set(it_extraction.desired_role_families)
        for fam in physical_fams:
            assert fam not in desired, f"Physical family '{fam}' must not be in desired_role_families"

    def test_physical_families_excluded(self, it_extraction):
        excluded = set(it_extraction.excluded_role_families)
        assert "warehouse" in excluded
        assert "production_manufacturing" in excluded
        assert "manual_labor" in excluded


# ---------------------------------------------------------------------------
# 4. Location and city
# ---------------------------------------------------------------------------

class TestLocationAndCity:
    def test_current_city_is_none(self, it_draft):
        """Berlin/Rostock/Hamburg are search regions, not current city."""
        assert it_draft.current_city is None

    def test_preferred_regions_contain_berlin(self, it_draft):
        assert "Berlin" in it_draft.preferred_regions

    def test_preferred_regions_contain_rostock(self, it_draft):
        assert "Rostock" in it_draft.preferred_regions

    def test_preferred_regions_contain_hamburg(self, it_draft):
        assert "Hamburg" in it_draft.preferred_regions

    def test_preferred_regions_contain_germany_wide(self, it_draft):
        assert "Deutschland" in it_draft.preferred_regions


# ---------------------------------------------------------------------------
# 5. Legal / authorization
# ---------------------------------------------------------------------------

class TestLegalAndAuthorization:
    def test_legal_status_section24(self, it_draft):
        assert it_draft.legal_status == "section_24"

    def test_work_authorized(self, it_draft):
        assert it_draft.work_authorized is True


# ---------------------------------------------------------------------------
# 6. Language levels
# ---------------------------------------------------------------------------

class TestLanguageLevels:
    def test_german_basic(self, it_draft):
        assert it_draft.german_level == "basic"

    def test_english_intermediate(self, it_draft):
        assert it_draft.english_level == "intermediate"


# ---------------------------------------------------------------------------
# 7. Relocation and remote
# ---------------------------------------------------------------------------

class TestRelocation:
    def test_relocation_ready(self, it_draft):
        assert it_draft.willing_to_relocate is True

    def test_remote_allowed(self, it_extraction):
        assert it_extraction.remote_allowed is True

    def test_international_remote_allowed(self, it_extraction):
        assert it_extraction.international_remote_allowed is True


# ---------------------------------------------------------------------------
# 8. Physical work
# ---------------------------------------------------------------------------

class TestPhysicalWork:
    def test_physical_work_false(self, it_draft):
        assert it_draft.physical_work_ok is False


# ---------------------------------------------------------------------------
# 9. Driving license — does NOT produce Driver role
# ---------------------------------------------------------------------------

class TestDrivingLicense:
    def test_driving_license_b(self, it_draft):
        assert it_draft.driving_license == "B"

    def test_license_b_does_not_produce_driver_role(self, it_draft):
        desired_lower = {r.lower() for r in it_draft.desired_roles}
        assert "водитель" not in desired_lower
        assert "driver" not in desired_lower
        assert "fahrer" not in desired_lower


# ---------------------------------------------------------------------------
# 10. Validator: shift_ok is NOT a missing critical field for IT profile
# ---------------------------------------------------------------------------

class TestValidator:
    def test_shift_ok_not_in_missing_fields(self, it_analysis):
        """IT profile must not be blocked by shift_ok — not relevant for IT."""
        assert "shift_ok" not in it_analysis.missing_fields

    def test_work_authorized_not_in_missing_fields(self, it_analysis):
        """§24 → work_authorized=True → must not be asked."""
        assert "work_authorized" not in it_analysis.missing_fields

    def test_no_critical_missing_fields(self, it_analysis):
        """A complete IT profile should have no missing critical fields."""
        assert it_analysis.missing_fields == ()

    def test_no_questions_asked_about_shifts(self, it_analysis):
        questions_text = " ".join(it_analysis.questions).lower()
        assert "смен" not in questions_text
        assert "shift" not in questions_text

    def test_no_questions_asked_about_work_authorization(self, it_analysis):
        questions_text = " ".join(it_analysis.questions).lower()
        assert "разрешение" not in questions_text
        assert "work authorization" not in questions_text


# ---------------------------------------------------------------------------
# 11. Search query terms — German and English (extracted, not yet persisted)
# ---------------------------------------------------------------------------

EXPECTED_DE_TERMS = [
    "Python Entwickler",
    "Backend Entwickler",
    "FastAPI Entwickler",
    "Django Entwickler",
    "KI Entwickler",
    "LLM Integration",
    "MVP Entwickler",
]

EXPECTED_EN_TERMS = [
    "Python Developer",
    "Backend Developer",
    "FastAPI Developer",
    "Django Developer",
    "AI Agent Developer",
    "LLM Integration Developer",
    "Automation Developer",
    "Telegram Bot Developer",
    "MVP Developer",
    "Internal Tools Developer",
]


class TestSearchQueryTerms:
    def test_search_query_terms_not_empty(self, it_extraction):
        assert len(it_extraction.search_query_terms) > 0

    def test_search_query_terms_no_russian(self, it_extraction):
        """search_query_terms must be in German or English — no Cyrillic."""
        for term in it_extraction.search_query_terms:
            cyrillic_chars = [c for c in term if "\u0400" <= c <= "\u04ff"]
            assert not cyrillic_chars, (
                f"search_query_terms must not contain Cyrillic: '{term}'"
            )

    def test_german_terms_present(self, it_extraction):
        terms_lower = {t.lower() for t in it_extraction.search_query_terms}
        for expected in EXPECTED_DE_TERMS:
            assert expected.lower() in terms_lower, (
                f"Expected German term '{expected}' in search_query_terms. "
                f"Got: {sorted(it_extraction.search_query_terms)}"
            )

    def test_english_terms_present(self, it_extraction):
        terms_lower = {t.lower() for t in it_extraction.search_query_terms}
        for expected in EXPECTED_EN_TERMS:
            assert expected.lower() in terms_lower, (
                f"Expected English term '{expected}' in search_query_terms. "
                f"Got: {sorted(it_extraction.search_query_terms)}"
            )

    def test_negative_terms_no_russian(self, it_extraction):
        for term in it_extraction.negative_query_terms:
            cyrillic_chars = [c for c in term if "\u0400" <= c <= "\u04ff"]
            assert not cyrillic_chars, (
                f"negative_query_terms must not contain Cyrillic: '{term}'"
            )

    def test_negative_terms_contain_physical_roles(self, it_extraction):
        neg_lower = " ".join(it_extraction.negative_query_terms).lower()
        assert any(k in neg_lower for k in ("lager", "produktion", "helfer", "warehouse", "production")), (
            f"Expected physical-role negative terms. Got: {it_extraction.negative_query_terms}"
        )


# ---------------------------------------------------------------------------
# 12. Russian roles must NOT become German search queries directly
# ---------------------------------------------------------------------------

class TestNoRussianInSearchQuery:
    def test_normalize_russian_склад_returns_german(self):
        """'Склад' must normalize to German keyword, not pass through as Russian."""
        query = normalize_query_from_roles(["Склад"])
        assert query is not None
        assert not any("\u0400" <= c <= "\u04ff" for c in (query or "")), (
            f"normalize_query_from_roles('Склад') returned Cyrillic: '{query}'"
        )
        assert query == "lager", f"Expected 'lager', got '{query}'"

    def test_normalize_russian_логистика_returns_german(self):
        query = normalize_query_from_roles(["Логистика"])
        assert query == "logistik"

    def test_normalize_russian_производство_returns_german(self):
        query = normalize_query_from_roles(["Производство"])
        assert query == "produktion"

    def test_normalize_russian_водитель_returns_german(self):
        query = normalize_query_from_roles(["Водитель"])
        assert query == "fahrer"

    def test_it_role_english_stays_as_is(self):
        """IT role in English → returned as-is (first word), no translation needed."""
        query = normalize_query_from_roles(["Python Developer"])
        assert query == "Python"

    def test_it_role_english_fastapi(self):
        query = normalize_query_from_roles(["FastAPI Developer"])
        assert query == "FastAPI"

    def test_it_role_search_query_not_russian(self):
        """After intake, search_query_de for IT profile must not be Russian."""
        it_roles = ["Python Developer", "Backend Developer", "AI Agent Developer"]
        query = normalize_query_from_roles(it_roles)
        assert query is not None
        cyrillic = [c for c in (query or "") if "\u0400" <= c <= "\u04ff"]
        assert not cyrillic, f"search_query_de must not be Cyrillic, got: '{query}'"


# ---------------------------------------------------------------------------
# 13. IT jobs with physical-sounding names must NOT be excluded by family classifier
# ---------------------------------------------------------------------------

class TestITJobsWithPhysicalNames:
    """Taxonomy must classify these as IT families, not physical families.

    These are software jobs that happen to reference physical domains.
    The profile_role_taxonomy.py keyword list must resolve them to IT families.
    """

    def test_warehouse_automation_software_developer_is_it(self):
        families = classify_roles(["Warehouse Automation Software Developer"])
        it_families = {
            RoleFamily.IT_SOFTWARE, RoleFamily.AI_AUTOMATION, RoleFamily.BACKEND,
            RoleFamily.SCRAPING_DATA, RoleFamily.FULLSTACK, RoleFamily.DEVOPS_INFRA,
            RoleFamily.ML_DATA, RoleFamily.TELEGRAM_BOTS, RoleFamily.FRONTEND_ONLY,
        }
        assert families & it_families, (
            f"'Warehouse Automation Software Developer' must be IT family. "
            f"Got: {families}"
        )
        assert RoleFamily.WAREHOUSE not in families, (
            "'Warehouse Automation Software Developer' must NOT be WAREHOUSE family"
        )

    def test_production_ml_engineer_is_ml(self):
        families = classify_roles(["Production ML Engineer"])
        assert RoleFamily.ML_DATA in families, (
            f"'Production ML Engineer' must be ML_DATA family. Got: {families}"
        )
        assert RoleFamily.PRODUCTION_MANUFACTURING not in families, (
            "'Production ML Engineer' must NOT be PRODUCTION_MANUFACTURING"
        )

    def test_logistics_software_developer_is_it(self):
        families = classify_roles(["Logistics Software Developer"])
        it_families = {
            RoleFamily.IT_SOFTWARE, RoleFamily.AI_AUTOMATION, RoleFamily.BACKEND,
            RoleFamily.FULLSTACK, RoleFamily.DEVOPS_INFRA, RoleFamily.ML_DATA,
        }
        assert families & it_families, (
            f"'Logistics Software Developer' must be IT family. Got: {families}"
        )

    def test_it_roles_not_excluded_by_physical_work_ok_false(self):
        """When physical_work_allowed=False, physical families are excluded.
        But 'Warehouse Automation Software Developer' (IT family) must survive."""
        from app.services.profile_extraction_model import ProfileExtractionResult
        extraction = ProfileExtractionResult(
            desired_roles=[
                "Python Developer",
                "Warehouse Automation Software Developer",
                "Production ML Engineer",
                "Logistics Software Developer",
            ],
            physical_work_allowed=False,
        )
        processed = post_process(extraction)
        assert "Warehouse Automation Software Developer" in processed.desired_roles, (
            "Warehouse Automation Software Developer is IT — must survive physical_work=False"
        )
        assert "Production ML Engineer" in processed.desired_roles, (
            "Production ML Engineer is ML/IT — must survive physical_work=False"
        )
        assert "Logistics Software Developer" in processed.desired_roles, (
            "Logistics Software Developer is IT — must survive physical_work=False"
        )


# ---------------------------------------------------------------------------
# 14. Downstream search query flow documentation test
# ---------------------------------------------------------------------------

class TestDownstreamSearchQueryFlow:
    """Documents what actually flows to job board APIs after save.

    Current architecture:
      ProfileExtractionResult.search_query_terms  → extracted, NOT persisted
      SearchProfile.search_query_de               → persisted, used as SourceSearchInput.query
      SourceSearchInput.query                      → sent to Adzuna/EURES/Careerjet/Remotive

    Gap: search_query_terms (multi-keyword DE/EN list) is not yet wired to search pipeline.
    search_query_de is a single term set from normalize_query_from_roles(desired_roles[0]).
    """

    def test_search_query_de_for_python_developer(self):
        """For IT profile, normalize_query_from_roles returns the English role as-is (first word)."""
        query = normalize_query_from_roles(["Python Developer"])
        assert query == "Python"

    def test_search_query_de_for_backend_developer(self):
        query = normalize_query_from_roles(["Backend Developer"])
        assert query == "Backend"

    def test_search_query_de_for_fastapi_developer(self):
        query = normalize_query_from_roles(["FastAPI Developer"])
        assert query == "FastAPI"

    def test_search_query_terms_flow_from_extraction_to_draft(self, it_analysis, it_extraction):
        """search_query_terms are extracted by LLM and propagated into IntakeProfileDraft."""
        # Extraction has the terms
        assert len(it_extraction.search_query_terms) > 0
        # Draft also carries them for persistence to SearchProfile
        assert hasattr(it_analysis.draft, "search_query_terms")
        assert len(it_analysis.draft.search_query_terms) > 0

    def test_search_query_de_is_not_russian(self, it_draft):
        """search_query_de (form pre-fill / primary query) must not contain Cyrillic."""
        effective_query = normalize_query_from_roles(list(it_draft.desired_roles))
        assert effective_query is not None
        assert not any("\u0400" <= c <= "\u04ff" for c in effective_query)
        assert len(effective_query) >= 2
