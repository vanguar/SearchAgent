"""
Full IT/MVP/AI Automation profile intake test.

Verifies that the parser + validator pipeline correctly handles:
- Desired IT roles extracted (Python Developer, AI Agent Developer, etc.)
- Excluded roles (Warehouse, Production, physical work) are NOT in desired
- Driving license B does not produce a Driver desired role
- Preferred regions (Berlin, Rostock, Hamburg, Germany-wide) are all captured
- current_city is NOT auto-set from preferred regions list
- shift_ok is not a critical field for IT profiles
- physical_work_ok = False
- §24 work authorization
- Language levels
"""
from __future__ import annotations

import pytest

from app.services.intake_models import IntakeProfileDraft
from app.services.profile_extraction_model import ProfileExtractionResult
from app.services.profile_parser import ProfileParser, extraction_result_to_draft
from app.services.profile_post_processor import process as post_process
from app.services.profile_validator import ProfileValidator


IT_PROFILE_TEXT = """\
Я хочу создать рабочий профиль для поиска IT/MVP/AI Automation проектов и вакансий.

Моя основная роль: AI-assisted Python Developer, MVP Builder, AI Agent Developer, Automation Developer.

Я занимаюсь созданием MVP, AI-агентов, внутренних бизнес-инструментов, автоматизации, backend-систем, API, Telegram-ботов, scraping/data pipelines и LLM-интеграций.

Основной стек: Python, FastAPI, Django, REST APIs, WebSockets, asyncio, PostgreSQL, SQLite, Redis, OpenAI API, Anthropic Claude, Deepgram, Telegram Bot API, scraping, automation, Railway, PythonAnywhere, VPS/Linux.

Мне интересны роли и проекты:
Python Developer,
MVP Developer,
AI Agent Developer,
Automation Developer,
Backend Developer,
FastAPI Developer,
Django Developer,
LLM Integration Developer,
Scraping / Data Extraction Developer,
Telegram Bot Developer,
Internal Tools Developer.

Не интересны роли без связи с Python/backend/AI/automation, чистый frontend без backend-логики, ручная физическая работа, склад, производство, низкоквалифицированные не-IT вакансии.

Страна поиска: Германия, но также рассматриваю международную дистанционную работу.

Предпочтительные регионы: Берлин, Росток, Гамбург, вся Германия. Также готов работать полностью remote из Германии.

Готовность к переезду: да, готов к переезду ради хорошей возможности. Особенно рассматриваю Берлин или другие города Германии с IT-рынком.

Формат работы: remote, hybrid или office, если есть смысл переезда. Предпочтительно remote/hybrid.

График: full-time, part-time или project-based. Сменный график для IT не является приоритетом, но гибкий график возможен.

Правовой статус: нахожусь в Германии по §24 AufenthG.

Разрешение на работу в Германии: да, есть разрешение на работу.

Немецкий язык: A1 / basic.

Английский язык: working proficiency, могу работать с технической документацией, кодом, задачами, API, перепиской и проектными описаниями.

Родные языки: украинский и русский.

Готовность начать: сразу / available immediately.

Физическая работа: нет, профиль создается для IT, MVP, AI automation и backend-разработки.

Жилье: если работа требует переезда в другой город, вопрос жилья желательно обсуждать отдельно, но для remote-работы жилье не требуется.

Водительские права: есть права категории B.

Личной машины в Германии нет.
"""


@pytest.fixture()
def it_profile_draft() -> IntakeProfileDraft:
    """Parse IT profile text without LLM (deterministic only)."""
    parser = ProfileParser(llm_client=None)
    return parser.parse(IT_PROFILE_TEXT)


@pytest.fixture()
def it_profile_draft_with_llm() -> IntakeProfileDraft:
    """IT profile built from a simulated LLM extraction → post-processing → draft."""
    extraction = ProfileExtractionResult(
        desired_roles=[
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
        excluded_roles=[
            "Warehouse",
            "Production",
            "manual physical work",
            "low-skilled non-IT jobs",
            "pure frontend without backend logic",
        ],
        german_level="basic",
        english_level="intermediate",
        legal_status="section_24",
        work_authorization=True,
        driving_license="B",
        has_car=False,
        physical_work_allowed=False,
        relocation_ready=True,
        availability="immediately",
        preferred_regions=["Berlin", "Rostock", "Hamburg", "Deutschland"],
        evidence_by_field={"current_city": ""},
    )
    processed = post_process(extraction, raw_text_lower=IT_PROFILE_TEXT.lower())
    return extraction_result_to_draft(processed)


# ---------------------------------------------------------------------------
# Desired roles — deterministic layer (no LLM)
# ---------------------------------------------------------------------------

class TestDesiredRolesDeterministic:
    def test_it_roles_present_without_llm(self, it_profile_draft: IntakeProfileDraft) -> None:
        desired = it_profile_draft.desired_roles
        assert "Python Developer" in desired
        assert "Backend Developer" in desired
        assert "FastAPI Developer" in desired
        assert "Django Developer" in desired
        assert "AI Automation Engineer" in desired
        assert "Telegram Bot Developer" in desired
        assert "Scraping / Data Extraction Developer" in desired

    def test_warehouse_not_in_desired_roles(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Склад" not in it_profile_draft.desired_roles

    def test_production_not_in_desired_roles(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Производство" not in it_profile_draft.desired_roles

    def test_driver_not_in_desired_roles(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Водитель" not in it_profile_draft.desired_roles

    def test_packaging_not_in_desired_roles(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Упаковка" not in it_profile_draft.desired_roles


# ---------------------------------------------------------------------------
# Desired roles — with LLM
# ---------------------------------------------------------------------------

class TestDesiredRolesWithLLM:
    def test_it_roles_present(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        desired = it_profile_draft_with_llm.desired_roles
        assert "Python Developer" in desired
        assert "AI Agent Developer" in desired
        assert "Backend Developer" in desired

    def test_warehouse_not_in_desired_roles(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert "Склад" not in it_profile_draft_with_llm.desired_roles
        assert "Warehouse" not in it_profile_draft_with_llm.desired_roles

    def test_production_not_in_desired_roles(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert "Производство" not in it_profile_draft_with_llm.desired_roles
        assert "Production" not in it_profile_draft_with_llm.desired_roles

    def test_driver_not_in_desired_roles(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert "Водитель" not in it_profile_draft_with_llm.desired_roles
        assert "Driver" not in it_profile_draft_with_llm.desired_roles


# ---------------------------------------------------------------------------
# Excluded roles
# ---------------------------------------------------------------------------

class TestExcludedRoles:
    def _excluded_lower(self, draft: IntakeProfileDraft) -> str:
        return " ".join(draft.excluded_roles).lower()

    def test_warehouse_not_in_desired_deterministic(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Склад" not in it_profile_draft.desired_roles
        assert "warehouse" in self._excluded_lower(it_profile_draft)

    def test_production_not_in_desired_deterministic(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Производство" not in it_profile_draft.desired_roles
        assert "production" in self._excluded_lower(it_profile_draft)

    def test_warehouse_in_excluded_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        excluded = self._excluded_lower(it_profile_draft_with_llm)
        assert "warehouse" in excluded or "склад" in excluded

    def test_physical_work_in_excluded_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        excluded = self._excluded_lower(it_profile_draft_with_llm)
        assert "physical" in excluded or "физическ" in excluded or "склад" in excluded


# ---------------------------------------------------------------------------
# Documents and equipment
# ---------------------------------------------------------------------------

class TestDocumentsAndEquipment:
    def test_driving_license_b(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.driving_license == "B"

    def test_no_car(self, it_profile_draft: IntakeProfileDraft) -> None:
        # "Личной машины в Германии нет" — conservative parser may not match this pattern
        assert it_profile_draft.has_car in {False, None}

    def test_driving_license_b_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.driving_license == "B"

    def test_no_car_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.has_car is False


# ---------------------------------------------------------------------------
# Legal status and work authorization
# ---------------------------------------------------------------------------

class TestLegalAndAuthorization:
    def test_legal_status_section24(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.legal_status == "section_24"

    def test_work_authorized(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.work_authorized is True

    def test_legal_status_section24_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.legal_status == "section_24"

    def test_work_authorized_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.work_authorized is True


# ---------------------------------------------------------------------------
# Language levels
# ---------------------------------------------------------------------------

class TestLanguageLevels:
    def test_german_basic(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.german_level in {"basic", "none"}

    def test_english_at_least_basic(self, it_profile_draft: IntakeProfileDraft) -> None:
        # "working proficiency" → intermediate; parser may detect "working" or "intermediate"
        assert it_profile_draft.english_level in {"basic", "intermediate", "advanced"}

    def test_german_basic_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.german_level == "basic"

    def test_english_intermediate_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.english_level == "intermediate"


# ---------------------------------------------------------------------------
# Relocation and regions
# ---------------------------------------------------------------------------

class TestRelocationAndRegions:
    def test_relocation_ready(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.willing_to_relocate is True

    def test_preferred_regions_contain_berlin(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Berlin" in it_profile_draft.preferred_regions

    def test_preferred_regions_contain_rostock(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Rostock" in it_profile_draft.preferred_regions

    def test_preferred_regions_contain_hamburg(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Hamburg" in it_profile_draft.preferred_regions

    def test_preferred_regions_contain_germany_wide(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert "Deutschland" in it_profile_draft.preferred_regions

    def test_current_city_not_auto_set_from_regions(self, it_profile_draft: IntakeProfileDraft) -> None:
        """Berlin is a preferred search region, not the user's stated city of residence."""
        assert it_profile_draft.current_city is None

    def test_current_city_not_set_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.current_city is None


# ---------------------------------------------------------------------------
# Physical work and availability
# ---------------------------------------------------------------------------

class TestPhysicalWorkAndAvailability:
    def test_physical_work_false(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.physical_work_ok is False

    def test_availability_immediately(self, it_profile_draft: IntakeProfileDraft) -> None:
        assert it_profile_draft.start_availability in {"Сразу", "immediately"}

    def test_physical_work_false_with_llm(self, it_profile_draft_with_llm: IntakeProfileDraft) -> None:
        assert it_profile_draft_with_llm.physical_work_ok is False


# ---------------------------------------------------------------------------
# Validator: shift_ok not critical for IT profile
# ---------------------------------------------------------------------------

class TestValidatorITProfile:
    def test_no_missing_fields_for_deterministic_it_profile(
        self, it_profile_draft: IntakeProfileDraft
    ) -> None:
        validator = ProfileValidator()
        result = validator.validate(it_profile_draft)
        assert result.missing_critical_fields == ()

    def test_shift_ok_not_in_missing_fields_for_it_profile(
        self, it_profile_draft_with_llm: IntakeProfileDraft
    ) -> None:
        """IT profile with roles like Python Developer must not require shift_ok."""
        validator = ProfileValidator()
        result = validator.validate(it_profile_draft_with_llm)
        assert "shift_ok" not in result.missing_critical_fields

    def test_shift_ok_still_critical_for_warehouse_profile(self) -> None:
        """Non-IT profile (warehouse) must still require shift_ok."""
        validator = ProfileValidator()
        draft = IntakeProfileDraft(
            desired_roles=["Склад", "Упаковка"],
            preferred_regions=["Berlin"],
            willing_to_relocate=True,
            german_level="basic",
            work_authorized=True,
            # shift_ok intentionally omitted
        )
        result = validator.validate(draft)
        assert "shift_ok" in result.missing_critical_fields

    def test_it_profile_can_save_without_shift_ok(
        self, it_profile_draft_with_llm: IntakeProfileDraft
    ) -> None:
        """IT profile with all other fields filled must have no critical missing fields."""
        validator = ProfileValidator()
        result = validator.validate(it_profile_draft_with_llm)
        # shift_ok is None but must not be in missing (IT profile)
        assert it_profile_draft_with_llm.shift_ok is None
        assert "shift_ok" not in result.missing_critical_fields


# ---------------------------------------------------------------------------
# Negative test cases (edge cases)
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_case_rights_b_only_no_driver_role(self) -> None:
        """'У меня есть права B' → license only, no Driver in desired_roles."""
        parser = ProfileParser(llm_client=None)
        draft = parser.parse("У меня есть права B")
        assert draft.driving_license == "B"
        assert "Водитель" not in draft.desired_roles

    def test_case_explicit_no_warehouse(self) -> None:
        """'Не хочу склад и производство' → excluded, not desired."""
        parser = ProfileParser(llm_client=None)
        draft = parser.parse("Не хочу склад и производство")
        assert "Склад" not in draft.desired_roles
        assert "Производство" not in draft.desired_roles

    def test_case_explicit_driver_job_intent(self) -> None:
        """Explicit driver intent captured via followup_answers in conservative mode."""
        parser = ProfileParser(llm_client=None)
        draft = parser.parse(
            "Ищу работу водителем, есть права B",
            followup_answers={"desired_roles": "Водитель"},
        )
        assert "Водитель" in draft.desired_roles
        assert draft.driving_license == "B"

    def test_case_python_backend_with_warehouse_exclusion(self) -> None:
        """Python backend desired, warehouse excluded — via post-processing pipeline."""
        extraction = ProfileExtractionResult(
            desired_roles=["Python Developer", "Backend Developer"],
            excluded_roles=["Склад"],
        )
        processed = post_process(extraction, raw_text_lower="хочу python backend remote, склад не интересует")
        draft = extraction_result_to_draft(processed)
        assert "Python Developer" in draft.desired_roles or "Backend Developer" in draft.desired_roles
        assert "Склад" not in draft.desired_roles

    def test_case_multiple_cities_preferred_regions(self) -> None:
        """'Берлин, Гамбург, вся Германия, готов к переезду' → regions set, city not auto-assigned."""
        parser = ProfileParser(llm_client=None)
        draft = parser.parse("Берлин, Гамбург, вся Германия, готов к переезду")
        assert "Berlin" in draft.preferred_regions
        assert "Hamburg" in draft.preferred_regions
        assert "Deutschland" in draft.preferred_regions
        assert draft.willing_to_relocate is True
        assert draft.current_city is None
