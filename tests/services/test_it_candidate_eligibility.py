"""Candidate requirements must dominate AI keyword relevance (owner policy, 2026-10-03)."""
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import pytest
from app.services.ai_tools_profile import AI_TOOLS_DESIRED_ROLES, AI_TOOLS_SEARCH_QUERY_TERMS
from app.services.filter_engine import FilterEngine
from app.services.normalization_models import (
    CanonicalVacancyGroup,
    LanguageSignals,
    NormalizedLocation,
    NormalizedVacancyRecord,
)
from app.services.normalizer import VacancyNormalizer
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_service import _assign_bucket
from app.services.source_adapters.models import SourceRecordPreview

PROFILE = SearchProfileContext(
    profile_label="AI automation", profile_source="saved", work_authorized=True,
    german_level="A1", english_level="A2", desired_roles=AI_TOOLS_DESIRED_ROLES,
    search_query_terms=AI_TOOLS_SEARCH_QUERY_TERMS, preferred_locations=("Remote Worldwide",),
)
TECH = "Python, FastAPI, REST API, webhooks, LLM agents, Claude Code, n8n AI automation. "


def evaluate(title, body, *, location="Remote Worldwide", profile=PROFILE, feedback=None):
    record = VacancyNormalizer().normalize_source_record(SourceRecordPreview(
        source_id="djinni_rss", source_name="Djinni", external_id="fixture", source_reference=None,
        title=title, company="Example", location=location, posted_at="2026-10-03",
        detail_url="https://example.org/job", raw_payload={"description":body}, description_complete=True,
    ))
    canonical = CanonicalVacancyGroup(
        canonical_key="fixture", normalized_title=record.normalized_title, company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text, country_code=record.normalized_location.country_code,
        city=record.normalized_location.city, posted_date=record.posted_date,
        language_signals=record.language_signals, source_records=(record,), provenance=(record.source_record_key,),
    )
    verdict = FilterEngine().evaluate(canonical, profile, search_mode="remote_worldwide")
    score = VacancyScorer().score(canonical, profile, filter_result=verdict, search_mode="remote_worldwide",
                                  today=date(2026,10,3), feedback_adjustment=feedback)
    return _assign_bucket(filter_result=verdict, score=score.score), verdict, score


@pytest.mark.parametrize("requirement", [
    "1+ year experience or substantial personal projects",
    "1+ year hands-on software development experience (commercial OR substantial personal projects)",
    "No commercial experience required. Personal projects accepted.",
    "Considering candidates with no experience. Mentorship under Tech Lead.",
])
def test_personal_projects_are_eligible(requirement):
    bucket, verdict, score = evaluate("Junior AI Developer", TECH + requirement)
    assert bucket == "hot"
    assert not verdict.hard_reject
    assert any(h.code == "it_entry_path" for h in score.positive_hits)


@pytest.mark.parametrize("requirement,ceiling", [
    ("1+ year commercial experience required", 69),
    ("1–2 years professional development experience required", 69),
    ("2+ years commercial experience required", 55),
    ("3+ years commercial experience required", 44),
    ("5–7 years production experience required", 44),
    ("Extensive commercial experience required", 44),
    ("Proven production experience required", 55),
    ("Several years of production experience required", 44),
    ("Досвід комерційної розробки від 3 років", 44),
    ("Опыт коммерческой разработки от 2 лет", 55),
])
def test_commercial_gap_cannot_be_outweighed(requirement, ceiling):
    bucket, _, score = evaluate("AI Integration Specialist", TECH * 3 + requirement, feedback=20)
    assert bucket != "hot"
    assert score.score <= ceiling
    assert any(h.code == "it_experience_gap" for h in score.negative_hits)


@pytest.mark.parametrize("title,body", [
    ("Senior AI Engineer", "5+ years production experience, architecture, AWS, Kafka."),
    ("Middle DS / AI Engineer", "3+ years Data Science/ML, PyTorch, statistics and model training."),
    ("Applied Scientist", "Model training and deep learning research."),
    ("Senior/Lead Full Stack GenAI Engineer", "React, Node, TypeScript, cloud production experience, commercial full-stack experience."),
    ("Senior Finance Manager", "Financial reporting. Claude Code is a useful tool."),
])
def test_ineligible_role_is_not_rescued_by_ai_tools(title, body):
    bucket, _, score = evaluate(title, TECH + body)
    assert bucket == "rejected"
    assert score.score <= 44


@pytest.mark.parametrize("language", [
    "Strong spoken English B2 required", "Strong English communication, both written and spoken",
    "Fluent English required", "Advanced English C1", "English B1 spoken required",
    "Regular communication with US/UK clients in English",
    "Англійська B2 для спілкування з клієнтами",
])
def test_language_is_stretch_not_automatic_rejection(language):
    bucket, verdict, score = evaluate("Junior AI Developer", TECH + "Personal projects accepted. " + language)
    assert bucket == "maybe"
    assert not verdict.hard_reject
    assert score.score <= 69
    assert any(h.code == "it_english_review" for h in verdict.review_hits)


@pytest.mark.parametrize("language", [
    "English for reading documentation", "Written English", "Basic English A2",
    "English B2 preferred, not required", "English courses with a native speaker",
])
def test_reading_or_optional_english_does_not_block_hot(language):
    assert evaluate("AI Integration Specialist", TECH + language)[0] == "hot"


@pytest.mark.parametrize("location", ["Remote Worldwide", "EU Remote", "Europe Remote", "Remote from Germany"])
def test_eligible_remote_location(location):
    bucket, _, score = evaluate("AI Integration Specialist", TECH, location=location)
    assert bucket == "hot"
    assert any(h.code == "it_remote_fit" for h in score.positive_hits)


@pytest.mark.parametrize("restriction", [
    "Must reside in Ukraine", "Mandatory hybrid Kyiv", "Office Kyiv, on-site required",
    "Обов'язкове проживання в Україні", "Обязательная работа в офисе в Киеве",
])
def test_explicit_ukraine_presence_is_rejected(restriction):
    bucket, verdict, _ = evaluate("AI Integration Specialist", TECH + restriction)
    assert bucket == "rejected"
    assert any(h.code == "it_location_mismatch" for h in verdict.rejection_hits)


def test_ukraine_remote_is_uncertain_but_company_country_is_not_restriction():
    assert evaluate("AI Integration Specialist", TECH, location="Ukraine Remote")[0] == "maybe"
    assert evaluate("AI Integration Specialist", TECH + "Company based in Ukraine. Work remotely from Europe.")[0] == "hot"


def test_senior_word_alone_and_optional_experience_are_not_vetoes():
    assert evaluate("Senior AI Integration Specialist", TECH + "Personal projects accepted.")[0] == "hot"
    assert evaluate("AI Integration Specialist", TECH + "3+ years commercial experience nice to have.")[0] == "hot"


def test_junior_or_pet_projects_do_not_cancel_separate_mandatory_experience():
    assert evaluate("Junior AI Developer", TECH + "Personal projects welcome. 3+ years commercial experience required.")[0] != "hot"


def test_python_automation_profile_has_same_eligibility_protection():
    p = replace(PROFILE, desired_roles=("Python Developer", "AI Automation Developer"),
                search_query_terms=("Python Developer", "FastAPI", "Django"))
    assert evaluate("Senior AI Engineer", TECH + "5+ years production experience required", profile=p)[0] != "hot"


def test_technical_data_automation_with_production_and_english_is_stretch():
    assert evaluate("Technical Data Automation Specialist", TECH + "Strong SQL, ETL production experience required. English B2.")[0] == "maybe"


def test_optional_sections_and_professional_growth_are_not_experience_requirements():
    body = TECH + "\nSignificant Advantage\nExperience in production systems\nWhat we offer:\nProfessional development and personal growth"
    bucket, _, score = evaluate("AI Integration Specialist", body)
    assert bucket == "hot"
    assert not any(h.code == "it_experience_gap" for h in score.negative_hits)


@pytest.mark.parametrize("requirement", ["High command of SQL", "React experience required", "Production AWS expertise required"])
def test_missing_stack_depth_requires_review(requirement):
    assert evaluate("AI Integration Specialist", TECH + requirement)[0] == "maybe"


def test_ml_core_is_not_automation_even_when_title_is_junior():
    assert evaluate("Junior Machine Learning Engineer", TECH + "Statistics, model training and PyTorch required.")[0] == "rejected"


def test_employer_history_is_not_candidate_experience():
    assert evaluate("AI Integration Specialist", TECH + "Our company has 10 years of commercial experience.")[0] == "hot"


def test_optional_office_is_not_mandatory_presence():
    assert evaluate("AI Integration Specialist", TECH + "No mandatory hybrid Kyiv. Work remote worldwide.")[0] == "hot"


def test_known_fluent_english_is_not_a_language_gap():
    assert evaluate("AI Integration Specialist", TECH + "Fluent English required.", profile=replace(PROFILE, english_level="C1"))[0] == "hot"


@pytest.mark.parametrize("case", json.loads((Path(__file__).parents[1] / "fixtures/it_eligibility_golden.json").read_text(encoding="utf-8")), ids=lambda c: c["id"])
def test_real_minimized_golden(case):
    bucket, verdict, score = evaluate(case["title"], case["body"], location="Remote")
    assert bucket == case["bucket"]
    assert case["score_range"][0] <= score.score <= case["score_range"][1]
    assert case["reason"] in {h.code for h in (*verdict.rejection_hits, *verdict.review_hits)}
    assert case["positive"] in {h.code for h in score.positive_hits}


@pytest.mark.parametrize("case", json.loads((Path(__file__).parents[1] / "fixtures/it_non_target_baseline.json").read_text(encoding="utf-8")), ids=lambda c: c["profile"]["profile_label"])
def test_non_it_results_exactly_match_prechange_baseline(case):
    profile = SearchProfileContext(**{k: tuple(v) if isinstance(v, list) else v for k, v in case["profile"].items()})
    payload = dict(case["canonical"])
    records = []
    for raw in payload["source_records"]:
        record = dict(raw)
        record["posted_date"] = date.fromisoformat(raw["posted_date"]) if raw["posted_date"] else None
        record["normalized_location"] = NormalizedLocation(**raw["normalized_location"])
        record["language_signals"] = LanguageSignals(**raw["language_signals"])
        records.append(NormalizedVacancyRecord(**record))
    payload["source_records"] = tuple(records)
    payload["posted_date"] = date.fromisoformat(payload["posted_date"]) if payload["posted_date"] else None
    payload["language_signals"] = LanguageSignals(**payload["language_signals"])
    canonical = CanonicalVacancyGroup(**payload)
    verdict = FilterEngine().evaluate(canonical, profile, search_mode="germany_local")
    score = VacancyScorer().score(canonical, profile, filter_result=verdict, search_mode="germany_local", today=date(2026, 10, 3))
    actual = {"score": score.score, "bucket": _assign_bucket(filter_result=verdict, score=score.score),
              "filter": asdict(verdict), "scoring": asdict(score)}
    assert json.loads(json.dumps(actual)) == case["expected"]
