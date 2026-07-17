from app.services.filter_engine import FilterEngine
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_service import _assign_bucket
from app.services.source_adapters.models import SourceRecordPreview


def _build_profile(**overrides: object) -> SearchProfileContext:
    payload = {
        "profile_label": "Основной поиск",
        "profile_source": "saved",
        "note_ru": "Используется сохраненный профиль поиска.",
        "legal_status": "Section 24",
        "work_authorized": True,
        "german_level": "basic",
        "desired_roles": ("склад", "логистика", "упаковка", "производство"),
        "excluded_roles": (),
        "preferred_locations": (),
        "relocation_ready": True,
        "shift_ok": True,
        "physical_work_ok": True,
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


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


def test_video_montage_does_not_false_match_production_family() -> None:
    """"montage" (video editing) must NOT count as a production role for a warehouse profile."""
    from app.services.rule_catalog import inspect_vacancy

    profile = _build_profile(desired_roles=("Lagerarbeiter", "Lagermitarbeiter"))
    canonical = _build_canonical(
        title="Mid/Senior AI Cinematic Video Editor",
        body="Video editor for cinematic montage and post-production of AI video.",
    )

    signals = inspect_vacancy(canonical, profile)

    assert not any(hit.code == "production_family" for hit in signals.positive_role_hits)


def test_german_assembly_still_matches_production_family() -> None:
    """Genuine German assembly/production keywords must still score as production work."""
    from app.services.rule_catalog import inspect_vacancy

    profile = _build_profile()
    for title, body in (
        ("Montagemitarbeiter (m/w/d)", "Montage von Bauteilen in der Fertigung."),
        ("Produktionshelfer (m/w/d)", "Mitarbeit in der Produktion, Schichtarbeit."),
    ):
        signals = inspect_vacancy(_build_canonical(title=title, body=body), profile)
        assert any(hit.code == "production_family" for hit in signals.positive_role_hits), title


def test_scorer_skips_location_penalty_in_remote_worldwide_mode() -> None:
    """Worldwide-remote must not penalize a geographic 'mismatch' — mirrors FilterEngine."""
    filter_engine = FilterEngine()
    scorer = VacancyScorer(filter_engine=filter_engine)
    profile = _build_profile(
        desired_roles=("Python Developer",),
        preferred_locations=("München",),
        relocation_ready=False,
    )
    canonical = _build_canonical(title="Python Developer", body="Build Python backend services.")

    filter_result = filter_engine.evaluate(canonical, profile, search_mode="remote_worldwide")
    result = scorer.score(canonical, profile, filter_result=filter_result, search_mode="remote_worldwide")

    assert not filter_result.hard_reject
    assert not any(hit.code == "location_mismatch" for hit in result.negative_hits)


def test_scorer_rewards_low_barrier_shift_and_relocation_signals() -> None:
    filter_engine = FilterEngine()
    scorer = VacancyScorer(filter_engine=filter_engine)
    profile = _build_profile()
    canonical = _build_canonical(
        title="Lagermitarbeiter/in",
        body="Ohne Deutsch. 3 Schicht. Unterkunft vorhanden. Ab sofort. Keine Erfahrung notwendig.",
    )
    filter_result = filter_engine.evaluate(canonical, profile)

    result = scorer.score(canonical, profile, filter_result=filter_result)

    assert result.score >= 90
    positive_codes = {hit.code for hit in result.positive_hits}
    assert {"priority_role", "low_language_signal", "shift_signal", "relocation_signal", "immediate_start_signal"} <= positive_codes


def test_scorer_penalizes_strong_german_and_requirement_mismatch() -> None:
    filter_engine = FilterEngine()
    scorer = VacancyScorer(filter_engine=filter_engine)
    profile = _build_profile(german_level="A1")
    canonical = _build_canonical(
        title="Lagermitarbeiter/in",
        body="Gute Deutschkenntnisse erforderlich. Abgeschlossene Ausbildung erforderlich. Mehrjahrige Erfahrung erforderlich.",
    )
    filter_result = filter_engine.evaluate(canonical, profile)

    result = scorer.score(canonical, profile, filter_result=filter_result)

    assert filter_result.decision == "reject"
    assert result.score <= 35
    negative_codes = {hit.code for hit in result.negative_hits}
    assert {"strong_german_mismatch", "vocational_requirement", "experience_requirement"} <= negative_codes


def _build_it_profile(**overrides: object) -> SearchProfileContext:
    payload = {
        "profile_label": "Python/AI Backend",
        "profile_source": "saved",
        "note_ru": "Используется сохраненный профиль поиска.",
        "legal_status": "section_24",
        "work_authorized": True,
        "german_level": "A1",
        "english_level": "intermediate",
        "desired_roles": (
            "Python Developer",
            "Backend Developer",
            "FastAPI Developer",
            "Django Developer",
            "AI Agent Developer",
            "Automation Developer",
        ),
        "excluded_roles": ("Warehouse", "Production", "manual physical work"),
        "preferred_locations": ("Berlin", "Deutschland"),
        "relocation_ready": True,
        "shift_ok": None,
        "physical_work_ok": False,
        "search_query_terms": (
            "Python Entwickler",
            "Backend Entwickler",
            "FastAPI Entwickler",
            "Django Entwickler",
            "KI Entwickler",
            "Python Developer",
            "Backend Developer",
            "AI Agent Developer",
            "Automation Developer",
            "API Developer",
        ),
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


def _score_it(title: str, body: str) -> tuple[int, str, set[str], set[str]]:
    filter_engine = FilterEngine()
    scorer = VacancyScorer(filter_engine=filter_engine)
    profile = _build_it_profile()
    canonical = _build_canonical(title=title, body=body)
    filter_result = filter_engine.evaluate(canonical, profile)
    score_result = scorer.score(canonical, profile, filter_result=filter_result)
    bucket = _assign_bucket(filter_result=filter_result, score=score_result.score)
    return (
        score_result.score,
        bucket,
        {hit.code for hit in score_result.positive_hits},
        {hit.code for hit in score_result.negative_hits},
    )


def test_python_java_backend_scores_above_dotnet_csharp_backend() -> None:
    python_score, _, python_hits, _ = _score_it(
        "Backend Entwickler Java/Python remote",
        "Backend APIs mit Python, Java und REST. Remote aus Berlin oder Deutschland.",
    )
    dotnet_score, dotnet_bucket, _, dotnet_negatives = _score_it(
        ".NET Backend Entwickler C#",
        ".NET C# backend services for accounting software in Berlin.",
    )

    assert python_score > dotnet_score
    assert dotnet_bucket == "maybe"
    assert "stack_python" in python_hits
    assert "stack_dotnet_csharp_only" in dotnet_negatives


def test_ki_backend_scores_above_php_backend() -> None:
    ki_score, _, ki_hits, _ = _score_it(
        "Backend Developer im KI Bereich",
        "Backend APIs fur KI, LLM und OpenAI Integrationen in Berlin.",
    )
    php_score, php_bucket, _, php_negatives = _score_it(
        "PHP Backend Developer",
        "PHP Symfony backend development for an internal platform in Berlin.",
    )

    assert ki_score > php_score
    assert php_bucket == "maybe"
    assert "stack_ai_llm" in ki_hits
    assert "stack_php_only" in php_negatives


def test_python_fastapi_backend_developer_berlin_is_hot() -> None:
    score, bucket, hits, _ = _score_it(
        "Python FastAPI Backend Developer Berlin",
        "Build REST APIs with Python, FastAPI, PostgreSQL and automation tools.",
    )

    assert bucket == "hot"
    assert score >= 70
    assert {"stack_python", "stack_fastapi", "stack_api_rest", "core_stack_hot_combo"} <= hits


def test_django_python_backend_developer_remote_germany_is_hot() -> None:
    score, bucket, hits, _ = _score_it(
        "Django Python Backend Developer Remote Germany",
        "Remote backend role in Germany using Django, Python and REST APIs.",
    )

    assert bucket == "hot"
    assert score >= 70
    assert {"stack_python", "stack_django", "stack_api_rest", "core_stack_hot_combo"} <= hits


def test_devops_only_stays_lower_maybe_not_hot() -> None:
    score, bucket, _hits, negatives = _score_it(
        "Senior DevOps Engineer Azure DevOps",
        "Azure DevOps, Kubernetes, Terraform and CI/CD operations.",
    )

    assert bucket in {"maybe", "rejected"}
    assert bucket != "hot"
    assert score < 70
    assert "title_devops_only" in negatives


def test_ai_engineer_scores_above_devops_only_even_when_devops_body_mentions_stack() -> None:
    ai_score, ai_bucket, ai_hits, _ = _score_it(
        "Senior Independent AI Engineer / Architect",
        "AI, LLM, OpenAI, REST APIs, startup MVP and internal tools.",
    )
    devops_score, devops_bucket, _hits, devops_negatives = _score_it(
        "Senior DevOps Engineer",
        "Marketplace text mentions Python, AI, REST APIs, automation and MVP projects, but this role is DevOps.",
    )

    assert ai_score > devops_score
    assert ai_bucket == "hot"
    assert devops_bucket != "hot"
    assert "stack_ai_llm" in ai_hits
    assert "title_devops_only" in devops_negatives


def test_head_of_engineering_is_lower_than_hands_on_ai_engineer() -> None:
    ai_score, _, _hits, _ = _score_it(
        "Senior Independent AI Engineer / Architect",
        "Hands-on AI, LLM, OpenAI and REST API development for MVP products.",
    )
    head_score, head_bucket, _head_hits, head_negatives = _score_it(
        "Head of Engineering",
        "Engineering leadership for startup MVP teams with AI and API products.",
    )

    assert ai_score > head_score
    assert head_bucket != "hot"
    assert "title_head_manager" in head_negatives
