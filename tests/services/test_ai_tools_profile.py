from __future__ import annotations

from app.db.models.profiles import SearchProfile, UserProfile
from app.services.ai_tools_profile import (
    AI_TOOLS_DESIRED_ROLES,
    AI_TOOLS_PROFILE_NAME,
    AI_TOOLS_SEARCH_QUERY_TERMS,
    is_ai_tools_profile,
)
from app.services.ai_tools_profile_service import AIToolsProfileService
from app.services.filter_engine import FilterEngine
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.rule_catalog import inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_service import _assign_bucket, _resolve_profile_search_terms
from app.services.source_adapters.models import SourceRecordPreview
from sqlalchemy.orm import Session


def _profile(**overrides: object) -> SearchProfileContext:
    payload: dict[str, object] = {
        "profile_label": AI_TOOLS_PROFILE_NAME,
        "profile_source": "saved",
        "legal_status": "section_24",
        "work_authorized": True,
        "german_level": "basic",
        "english_level": None,
        "desired_roles": AI_TOOLS_DESIRED_ROLES,
        "excluded_roles": (),
        "preferred_locations": ("Rostock", "Stralsund", "Greifswald"),
        "relocation_ready": True,
        "no_german_required": True,
        "search_query_terms": AI_TOOLS_SEARCH_QUERY_TERMS,
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


def _canonical(
    *,
    title: str,
    body: str,
    source_id: str = "remotive",
    location: str = "Remote",
) -> CanonicalVacancyGroup:
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id=source_id,
            source_name=source_id.upper(),
            external_id=f"{source_id}-ai-tools",
            source_reference=f"{source_id}-ai-tools",
            title=title,
            company="Automation Lab",
            location=location,
            posted_at="2026-08-30",
            detail_url="https://example.org/ai-tools",
            raw_payload={"description": body},
        )
    )
    return CanonicalVacancyGroup(
        canonical_key=f"canonical-{source_id}-ai-tools",
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


def _evaluate(
    canonical: CanonicalVacancyGroup,
    *,
    profile: SearchProfileContext | None = None,
    search_mode: str = "remote_worldwide",
):
    resolved_profile = profile or _profile()
    signals = inspect_vacancy(canonical, resolved_profile)
    filter_engine = FilterEngine()
    filter_result = filter_engine.evaluate(
        canonical,
        resolved_profile,
        signals=signals,
        search_mode=search_mode,
    )
    score_result = VacancyScorer(filter_engine=filter_engine).score(
        canonical,
        resolved_profile,
        signals=signals,
        filter_result=filter_result,
        search_mode=search_mode,
    )
    bucket = _assign_bucket(filter_result=filter_result, score=score_result.score)
    return signals, filter_result, score_result, bucket


def test_profile_identity_is_field_based_and_does_not_capture_existing_python_profile() -> None:
    assert is_ai_tools_profile(_profile()) is True
    existing_python = _profile(
        profile_label="Python developer / Ai automation developer",
        desired_roles=("Python Developer", "AI Automation Developer"),
        search_query_terms=("Python Developer", "AI Automation Developer", "FastAPI", "Django"),
        no_german_required=False,
    )
    assert is_ai_tools_profile(existing_python) is False


def test_english_source_text_without_requirement_is_not_rejected_and_is_high() -> None:
    canonical = _canonical(
        title="AI Automation Specialist",
        body=(
            "Build n8n workflows using GPT and Claude for our internal operations team. "
            "You will connect existing SaaS tools, design prompt chains and keep the "
            "automations running day to day. No formal software engineering background "
            "required, and we do not ask for any specific language certificate."
        ),
        source_id="remotive",
    )

    signals, filter_result, score_result, bucket = _evaluate(canonical)

    assert signals.english_required_signal is False
    assert signals.ai_tools_language_fit_signal is True
    assert filter_result.hard_reject is False
    assert bucket == "hot"
    assert score_result.score >= 70
    assert any(
        hit.code == "ai_tools_language_fit_signal"
        and hit.label_ru == "английский и немецкий не указаны как обязательные"
        for hit in score_result.positive_hits
    )


def test_explicit_fluent_english_requirement_is_incompatible() -> None:
    canonical = _canonical(
        title="AI Workflow Specialist",
        body="Automate internal processes with LLM tools. Fluent English required.",
    )

    signals, filter_result, _, _ = _evaluate(canonical)

    assert signals.english_required_signal is True
    assert any(hit.code == "english_required_mismatch" for hit in filter_result.rejection_hits)


def test_english_preferred_is_soft_penalty_not_hard_reject() -> None:
    canonical = _canonical(
        title="Prompt Engineer / AI Workflow Specialist",
        body="Build AI workflows. English is preferred and is a plus.",
    )

    signals, filter_result, score_result, _ = _evaluate(canonical)
    _, _, baseline_score, _ = _evaluate(
        _canonical(
            title="Prompt Engineer / AI Workflow Specialist",
            body="Build AI workflows.",
        )
    )

    assert signals.english_preferred_signal is True
    assert filter_result.hard_reject is False
    assert any(hit.code == "english_preferred_signal" for hit in score_result.negative_hits)
    assert score_result.score < baseline_score.score


def test_claude_code_and_codex_agentic_workflow_are_strong_positive_signals() -> None:
    canonical = _canonical(
        title="AI Tooling Specialist",
        body="Use Claude Code, OpenAI Codex CLI and coding agents in agentic workflows.",
    )

    _, _, score_result, bucket = _evaluate(canonical)
    codes = {hit.code for hit in score_result.positive_hits}

    assert "ai_coding_brands" in codes
    assert "ai_agents_workflows" in codes
    assert bucket == "hot"


def test_n8n_llm_and_basic_scripting_are_positive() -> None:
    canonical = _canonical(
        title="n8n Automation Developer",
        body="Create n8n AI integrations with LLM APIs. Some scripting experience and basic Python are useful.",
    )

    _, filter_result, score_result, bucket = _evaluate(canonical)
    codes = {hit.code for hit in score_result.positive_hits}

    assert filter_result.hard_reject is False
    assert {"ai_automation_workflows", "ai_basic_technical_fit"}.issubset(codes)
    assert bucket in {"hot", "maybe"}


def test_senior_java_algorithms_and_c1_english_is_rejected() -> None:
    canonical = _canonical(
        title="Senior Java Engineer",
        body=(
            "5+ years commercial software development. Strong algorithms and data structures, "
            "advanced system design. English C1."
        ),
    )

    _, filter_result, score_result, _ = _evaluate(canonical)
    rejection_codes = {hit.code for hit in filter_result.rejection_hits}

    assert "english_required_mismatch" in rejection_codes
    assert "classic_engineering_mismatch" in rejection_codes
    assert score_result.score <= 35


def test_generic_developer_without_ai_signals_is_not_high_relevance() -> None:
    canonical = _canonical(
        title="Software Developer",
        body="Develop and maintain business software for internal customers.",
    )

    _, filter_result, score_result, bucket = _evaluate(canonical)

    assert filter_result.hard_reject is False
    assert bucket != "hot"
    assert score_result.score < 70


def test_russian_ai_automation_vacancy_is_relevant() -> None:
    canonical = _canonical(
        title="Специалист по нейросетям",
        body="Внедрение ИИ и автоматизация бизнес-процессов, создание AI-агентов без кода.",
        source_id="hh",
    )

    _, filter_result, score_result, bucket = _evaluate(canonical)

    assert filter_result.hard_reject is False
    assert any(hit.code.startswith("ai_") for hit in score_result.positive_hits)
    assert bucket == "hot"


def test_german_ki_automation_with_a1_is_relevant() -> None:
    canonical = _canonical(
        title="KI-Automatisierung Spezialist",
        body="Automatisierung mit KI und Generative AI. Deutsch A1 ist ausreichend.",
        source_id="arbeitnow",
        location="Rostock, Deutschland",
    )

    signals, filter_result, _, bucket = _evaluate(canonical, search_mode="germany_local")

    assert signals.basic_german_signal is True
    assert not any("german" in hit.code for hit in filter_result.rejection_hits)
    assert bucket == "hot"


def test_basic_api_knowledge_remains_compatible() -> None:
    canonical = _canonical(
        title="Technical AI Implementation Specialist",
        body="Integrate ready-made AI services. Basic API knowledge and webhooks are sufficient.",
    )

    _, filter_result, score_result, _ = _evaluate(canonical)

    assert filter_result.hard_reject is False
    assert any(hit.code == "ai_basic_technical_fit" for hit in score_result.positive_hits)


def test_remote_mode_ignores_local_profile_geography_but_local_mode_keeps_it() -> None:
    profile = _profile(preferred_locations=("Rostock",), relocation_ready=False)
    remote_job = _canonical(
        title="AI Agent Builder",
        body="Low-code AI agents and workflow automation.",
        location="Remote Worldwide",
    )

    _, remote_filter, _, _ = _evaluate(remote_job, profile=profile, search_mode="remote_worldwide")
    _, local_filter, _, _ = _evaluate(remote_job, profile=profile, search_mode="germany_local")

    assert not any(hit.code == "location_mismatch" for hit in remote_filter.rejection_hits)
    assert any(hit.code == "location_mismatch" for hit in local_filter.rejection_hits)


def test_query_plan_supports_western_and_russian_source_scopes() -> None:
    profile = _profile()

    western_terms = _resolve_profile_search_terms(profile, source_ids=("ba", "adzuna", "remotive"))
    russian_terms = _resolve_profile_search_terms(profile, source_ids=("hh", "dou_rss", "djinni_rss"))

    assert len(AI_TOOLS_SEARCH_QUERY_TERMS) == 16
    assert all(term.casefold() not in {"developer", "programmer", "software engineer", "it"} for term in AI_TOOLS_SEARCH_QUERY_TERMS)
    assert "Claude Code" in western_terms and "KI Automatisierung" in western_terms
    assert not any(any("а" <= char.casefold() <= "я" for char in term) for term in western_terms)
    assert "специалист по нейросетям" in russian_terms
    assert "AI интегратор" in russian_terms
    assert profile.search_query_terms == AI_TOOLS_SEARCH_QUERY_TERMS


def test_ai_tools_profile_preview_and_creation_preserve_existing_profile(db_session: Session) -> None:
    user = UserProfile(
        display_name="Основной профиль",
        country_code="DE",
        legal_status="section_24",
        work_authorized=True,
        german_level="basic",
        english_level=None,
    )
    db_session.add(user)
    db_session.flush()
    existing = SearchProfile(
        user_profile_id=user.id,
        name="Python developer / Ai automation developer",
        is_active=True,
        is_default=True,
        desired_roles=["Python Developer", "AI Automation Developer"],
        preferred_locations=["Rostock", "Stralsund", "Greifswald"],
        relocation_ready=True,
        search_query_terms=["Python Developer", "AI Automation Developer", "FastAPI", "Django"],
        search_location_de="Rostock",
    )
    db_session.add(existing)
    db_session.commit()

    service = AIToolsProfileService()
    preview = service.preview(db_session, user_profile_id=user.id)

    assert preview is not None
    assert preview.spec.name == AI_TOOLS_PROFILE_NAME
    assert preview.spec.preferred_locations == ("Rostock", "Stralsund", "Greifswald")
    assert preview.spec.no_german_required is True
    assert preview.spec.search_location_de == "Rostock"
    assert preview.search_modes == ("germany_local", "remote_worldwide")
    assert preview.source_scopes == ("western", "russian")

    result = service.create_from_preview(db_session, preview=preview)

    assert result.created is True
    assert result.profile is not None
    assert result.profile.name == AI_TOOLS_PROFILE_NAME
    assert result.profile.is_default is False
    assert tuple(result.profile.search_query_terms or ()) == AI_TOOLS_SEARCH_QUERY_TERMS
    db_session.refresh(existing)
    assert existing.name == "Python developer / Ai automation developer"
    assert existing.search_query_terms == ["Python Developer", "AI Automation Developer", "FastAPI", "Django"]

    duplicate = service.create_from_preview(db_session, preview=preview)
    assert duplicate.created is False
    assert db_session.query(SearchProfile).filter(SearchProfile.name == AI_TOOLS_PROFILE_NAME).count() == 1


def test_short_excerpt_does_not_earn_the_language_fit_bonus() -> None:
    """По вырезке нельзя утверждать, что язык не требуется.

    Careerjet отдаёт фрагмент вокруг поискового слова, Adzuna режет описание на 500
    символах. Раньше такое описание давало бонус "английский и немецкий не указаны
    как обязательные" просто потому, что требование не поместилось в кусок текста.
    """
    canonical = _canonical(
        title="AI Automation Specialist",
        body="Build n8n workflows using GPT.",
        source_id="careerjet",
    )

    signals, filter_result, score_result, _ = _evaluate(canonical)

    assert signals.english_requirement_unknown is True
    assert signals.ai_tools_language_fit_signal is False
    assert filter_result.hard_reject is False
    assert not any(hit.code == "ai_tools_language_fit_signal" for hit in score_result.positive_hits)


def test_english_mentioned_only_as_a_perk_is_not_a_requirement() -> None:
    """"English courses" в блоке бонусов — соцпакет, а не языковой барьер."""
    canonical = _canonical(
        title="AI Automation Specialist",
        body=(
            "Build AI automations with n8n and LLM agents for our operations team. "
            "What we offer: medical insurance, corporate events, english courses with "
            "a native speaker, flexible working hours and a yearly education budget."
        ),
    )

    signals, filter_result, _, _ = _evaluate(canonical)

    assert signals.english_required_signal is False
    assert signals.english_requirement_unknown is False
    assert filter_result.hard_reject is False


def test_cefr_and_worded_english_levels_are_read_as_requirements() -> None:
    """Реальные формулировки уровня из IT-объявлений, а не только "english required"."""
    for body in (
        "You will own the backend. Requirements: upper intermediate english level, "
        "both verbal and written, for daily communication with the customer team.",
        "Requirements: strong python skills and english b2 for comfortable verbal "
        "team communication across our distributed engineering group.",
        "Soft skills: good written and spoken english, ownership over micromanagement, "
        "and the ability to work independently in a remote environment.",
    ):
        canonical = _canonical(title="AI Automation Specialist", body=body)
        signals, filter_result, _, _ = _evaluate(canonical)
        assert signals.english_required_signal is True, body
        assert filter_result.hard_reject is True, body


def test_english_level_softened_by_nice_to_have_is_not_a_requirement() -> None:
    """"english b1 nice to have" — пожелание, а не барьер."""
    canonical = _canonical(
        title="AI Automation Specialist",
        body=(
            "You direct AI agents while keeping control of the code and shipping "
            "automations weekly. English b1 nice to have, healthcare domain experience "
            "is also welcome but not mandatory for this position."
        ),
    )

    signals, filter_result, _, _ = _evaluate(canonical)

    assert signals.english_required_signal is False
    assert filter_result.hard_reject is False
