"""2026-09-30 live export: minimal real excerpts plus explicit counterexamples.

The 16 hidden records were not persisted with their bodies. Tests of their exact
titles and exported generic vehicle signals are evidence-limited, not full replays.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from app.core.config import Settings
from app.services.employment_signal_extractor import extract_employment_signals
from app.services.filter_engine import FilterEngine
from app.services.normalizer import VacancyNormalizer
from app.services.role_family import RoleFamily, classify_role_text, classify_vacancy_de
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext, SearchRunResult
from app.services.search_service import SearchService, _assign_bucket, _pick_primary_record
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService
from app.services.vacancy_quality_signals import quality_differentiator_hits
from tests.services.relevance_support import staffing_agencies_allowed

TODAY = date(2026, 9, 30)
PROFILE = SearchProfileContext(
    profile_label="Перегон автомобилей", profile_source="saved", german_level="A1",
    english_level="none", desired_roles=("Fahrzeugüberführer", "Überführungsfahrer"),
    search_query_terms=("Fahrzeugüberführer", "Fahrzeugüberführung", "Überführungsfahrer"),
    driver_license="B", employment_types=("full_time",), self_employment_ok=False,
    physical_work_ok=False, relocation_ready=True,
)
FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/live_vehicle_logistics.json").read_text(encoding="utf-8"))


def live_record(external_id: str):
    preview = SourceRecordPreview(**next(r for r in FIXTURES if r["external_id"] == external_id))
    return VacancyNormalizer().normalize_source_record(preview)


def group(title="Fahrzeugüberführer", body="", *, complete=False):
    record = SourceRecordPreview(
        source_id="test", source_name="Test", external_id="1", source_reference=None,
        title=title, company="Example", location="Berlin", posted_at=TODAY.isoformat(),
        detail_url="https://example.org/1", raw_payload={"description": body}, description_complete=complete,
    )
    return SourceMergeService().merge_records((VacancyNormalizer().normalize_source_record(record),)).canonical_groups[0]


def evaluate(canonical, profile=PROFILE):
    signals = inspect_vacancy(canonical, profile)
    verdict = FilterEngine().evaluate(canonical, profile, signals=signals)
    score = VacancyScorer().score(canonical, profile, signals=signals, filter_result=verdict, today=TODAY)
    return signals, verdict, score


@pytest.mark.parametrize("external_id", ["5505362768", "5414701817"])
def test_real_detailers_are_not_transfer_jobs(external_id):
    canonical = SourceMergeService().merge_records((live_record(external_id),)).canonical_groups[0]
    signals, verdict, score = evaluate(canonical)
    assert classify_vacancy_de(canonical.normalized_title) is RoleFamily.CLEANING
    assert not signals.positive_role_hits_in_title
    assert not signals.desired_role_hits
    assert verdict.hard_reject
    assert "priority_role" not in {h.code for h in score.positive_hits}


@pytest.mark.parametrize("role", ["Fahrzeugaufbereiter", "Fahrzeugpfleger", "подготовка автомобилей"])
def test_explicit_detailing_profile_keeps_its_own_occupation(role):
    assert normalize_role_intent(role).family is RoleFamily.CLEANING
    _, verdict, _ = evaluate(group("Fahrzeugaufbereiter"), replace(PROFILE, desired_roles=(role,), search_query_terms=()))
    assert not verdict.hard_reject


@pytest.mark.parametrize("title", [
    "Fahrzeugüberführer", "Überführungsfahrer", "Fahrzeugüberführung", "Fahrzeugtransfer",
    "Hol- und Bringfahrer", "PKW-Überführer", "Mietwagenüberführer", "Überführer / Fahrer (m/w/d) Berliner Raum",
])
def test_transfer_titles_stay_transfer_and_not_courier(title):
    canonical = group(title)
    signals, verdict, _ = evaluate(canonical)
    assert classify_vacancy_de(canonical.normalized_title) is RoleFamily.VEHICLE_LOGISTICS
    assert signals.positive_role_hits_in_title
    assert not verdict.hard_reject
    _, delivery_verdict, _ = evaluate(canonical, replace(PROFILE, desired_roles=("Kurierfahrer",), search_query_terms=()))
    assert delivery_verdict.hard_reject


@pytest.mark.parametrize("title", ["Lieferfahrer", "Kurierfahrer", "Paketzusteller", "Fahrzeugführer - Elektrogeräte"])
def test_goods_delivery_is_not_transfer(title):
    assert classify_role_text(title) is RoleFamily.DRIVING


def test_real_appliance_delivery_body_does_not_override_the_title():
    canonical = SourceMergeService().merge_records((live_record("cj-066e8bae0fe78f87"),)).canonical_groups[0]
    assert "Herde, Backöfen, Waschmaschinen" in canonical.source_records[0].body_text
    signals, verdict, _ = evaluate(canonical)
    assert not signals.positive_role_hits
    assert not signals.desired_role_hits
    assert {h.code for h in verdict.rejection_hits} == {"profession_family_mismatch"}
    _, verdict, _ = evaluate(canonical, replace(PROFILE, desired_roles=("Lieferfahrer",), search_query_terms=()))
    assert not verdict.hard_reject


def test_mixed_transfer_and_delivery_title_remains_eligible():
    _, verdict, _ = evaluate(group("Auslieferungsfahrer / Überführer", "Auslieferung und Fahrzeugüberführung, Klasse B."))
    assert not verdict.hard_reject


@pytest.mark.parametrize("title,body", [
    ("Fahrzeugüberführer (m/w/d) (keine Zeitarbeit) Vollzeit", "Berufskraftfahrer/in"),
    ("Überführer / Fahrer (m/w/d) Berliner Raum", "Kraftfahrer"),
    ("Fahrzeugüberführer", "Kraftfahrer und Berufskraftfahrer gesucht."),
])
def test_exported_generic_driver_wording_cannot_prove_heavy_transport(title, body):
    signals, verdict, _ = evaluate(group(title, body))
    assert signals.heavy_vehicle_context_signals
    assert not signals.heavy_vehicle_signals
    assert not verdict.hard_reject


@pytest.mark.parametrize("body", [
    "Führerschein C zwingend erforderlich", "Führerschein CE erforderlich",
    "PKW und LKW fahren", "Fahrer für 7,5 t", "Fahrer für 40-Tonner",
    "Berufskraftfahrerqualifikation und Code 95 erforderlich", "Sattelzug fahren",
])
def test_actual_heavy_transport_remains_rejected(body):
    assert evaluate(group(body=body))[1].hard_reject


@pytest.mark.parametrize("body", [
    "CE nicht erforderlich, Klasse B genügt", "Kein LKW-Führerschein nötig, Klasse B genügt",
    "Berufskraftfahrerqualifikation nicht erforderlich", "Kraftfahrer, kein LKW, nur PKW",
])
def test_negated_heavy_requirements_remain_allowed(body):
    assert not evaluate(group(body=body))[1].hard_reject


def test_real_working_student_is_reviewed_against_saved_full_time_preference():
    canonical = SourceMergeService().merge_records((live_record("5802957060"),)).canonical_groups[0]
    signals, verdict, score = evaluate(canonical)
    assert signals.employment_types == ("working_student",)
    assert not signals.requires_self_employment
    assert "employment_type_review" in {h.code for h in verdict.review_hits}
    assert "employment_type_mismatch" in {h.code for h in score.negative_hits}
    assert not verdict.hard_reject
    _, unspecified, _ = evaluate(canonical, replace(PROFILE, employment_types=()))
    assert "employment_type_review" not in {h.code for h in unspecified.review_hits}


def test_student_negation_is_preserved():
    assert not extract_employment_signals("Keine Werkstudenten gesucht").employment_types


@pytest.mark.parametrize("reverse", [False, True])
def test_real_duplicate_retains_all_sources_and_fuller_text(reverse):
    records = (live_record("5890228465"), live_record("cj-f0c8caf613a29c78"))
    groups = SourceMergeService().merge_records(records[::-1] if reverse else records).canonical_groups
    assert len(groups) == 1
    canonical = groups[0]
    assert {r.source_id for r in canonical.source_records} == {"adzuna", "careerjet"}
    assert {r.source_url for r in canonical.source_records} == {r.source_url for r in records}
    assert canonical.posted_date == date(2026, 9, 19)
    assert _pick_primary_record(canonical).source_id == "careerjet"
    assert "12016-10005358982-S" in _pick_primary_record(canonical).body_text
    assert "B" in evaluate(canonical)[0].required_driver_license_categories


def test_duplicate_score_is_independent_of_source_order():
    records = (live_record("5890228465"), live_record("cj-f0c8caf613a29c78"))
    forward = SourceMergeService().merge_records(records).canonical_groups[0]
    backward = SourceMergeService().merge_records(records[::-1]).canonical_groups[0]
    assert evaluate(forward)[1:] == evaluate(backward)[1:]


def test_duplicate_found_in_separate_queries_is_rescored_and_keeps_all_urls():
    # Проверяется склейка и ссылки, а не политика агентств: вакансия от perZukunft.
    with staffing_agencies_allowed():
        _assert_duplicate_found_in_separate_queries_is_rescored_and_keeps_all_urls()


def _assert_duplicate_found_in_separate_queries_is_rescored_and_keeps_all_urls():
    service = SearchService()
    records = (live_record("5890228465"), live_record("cj-f0c8caf613a29c78"))
    attempts = []
    for record in records:
        canonical = SourceMergeService().merge_records((record,)).canonical_groups[0]
        item = service._build_result_item(canonical=canonical, profile=PROFILE, enrich_with_llm=False)
        attempts.append(SearchRunResult(
            profile=PROFILE, source_states=(), results=(item,), normalized_records=(record,),
            total_raw_records=1, total_normalized_records=1, total_canonical_results=1,
        ))
    result = service._merge_and_rescore_attempts(attempts, profile=PROFILE, search_mode="germany_local")
    assert result.total_canonical_results == 1
    assert len(result.results) == 1
    assert len(result.normalized_records) == 2
    item = result.results[0]
    assert item.primary_record.source_id == "careerjet"
    assert {r.source_url for r in item.canonical_group.source_records} == {r.source_url for r in records}
    assert item.score_result == service.scorer.score(item.canonical_group, PROFILE)


def test_ba_adzuna_and_careerjet_urls_survive_merge():
    careerjet = live_record("cj-f0c8caf613a29c78")
    ba = replace(careerjet, source_id="ba", external_id="ba-reference", source_url="https://example.org/ba/job")
    records = (ba, live_record("5890228465"), careerjet)
    canonical = SourceMergeService().merge_records(records).canonical_groups[0]
    assert {r.source_url for r in canonical.source_records} == {r.source_url for r in records}


def test_hidden_heavy_evidence_from_another_attempt_cannot_be_lost():
    service = SearchService()
    safe = group(body="Klasse B. Kein Deutsch erforderlich.").source_records[0]
    heavy = replace(safe, body_text="Klasse CE erforderlich. LKW fahren.", content_fingerprint="changed")
    attempts = [SearchRunResult(
        profile=PROFILE, source_states=(), results=(), normalized_records=(record,),
        total_normalized_records=1,
    ) for record in (safe, heavy)]
    result = service._merge_and_rescore_attempts(attempts, profile=PROFILE, search_mode="germany_local")
    assert not result.results
    assert len(result.hidden_filtered_items) == 1


@pytest.mark.parametrize("difference", ["city", "body"])
def test_similar_agency_jobs_are_not_merged(difference):
    first = live_record("cj-f0c8caf613a29c78")
    second = replace(first, source_id="adzuna", external_id="different", normalized_company="perzukunft arbeitsvermittlung")
    if difference == "city":
        second = replace(second, normalized_location=replace(second.normalized_location, city="Rostock"))
    else:
        second = replace(second, body_text="Andere Aufgaben bei einem anderen Kunden.")
    assert len(SourceMergeService().merge_records((first, second)).canonical_groups) == 2


@pytest.mark.parametrize("company", ["perzukunft", "perzukunft arbeitsvermittlung"])
def test_identical_posting_with_another_reference_becomes_one_card_keeping_both_links(company):
    # Одинаковый заголовок, работодатель, город и текст: одна карточка, а не
    # несколько одинаковых. Обе записи остаются внутри неё — ссылка не теряется.
    first = live_record("cj-f0c8caf613a29c78")
    second = replace(
        first, external_id="different", normalized_company=company,
        body_text=first.body_text.replace("12016-10005358982-S", "12016-10009999999-S"),
    )
    groups = SourceMergeService().merge_records((first, second)).canonical_groups
    assert len(groups) == 1
    assert {r.external_id for r in groups[0].source_records} == {first.external_id, "different"}


def test_real_incomplete_hot_is_capped_without_rejection():
    canonical = SourceMergeService().merge_records((live_record("12016-10005467345-S"),)).canonical_groups[0]
    signals, verdict, score = evaluate(canonical)
    assert signals.description_insufficient
    assert not verdict.hard_reject
    assert score.score == 69
    assert _assign_bucket(filter_result=verdict, score=score.score) == "maybe"


@pytest.mark.parametrize("title,body", [
    ("Fahrzeugüberführer Klasse B, ohne Deutsch, Festanstellung", ""),
    ("Fahrzeugüberführer", "Führerschein Klasse B erforderlich. Deutschkenntnisse von Vorteil. Festanstellung."),
    ("Fahrzeugüberführer", "Klasse B erforderlich. Keine Deutschkenntnisse erforderlich. Festanstellung."),
])
def test_short_description_with_known_requirements_can_be_hot(title, body):
    signals, verdict, score = evaluate(group(title, body))
    assert signals.description_insufficient
    assert not verdict.hard_reject
    assert score.score >= 70


def test_cap_respects_requirements_already_resolved_in_source_signals():
    canonical = group()
    signals = replace(inspect_vacancy(canonical, PROFILE),
                      required_driver_license_categories=("B",), german_not_required_signal=True,
                      employed_contract_signal=True)
    score = VacancyScorer().score(canonical, PROFILE, signals=signals, today=TODAY)
    assert score.score >= 70
    assert "incomplete_requirements_review" not in {h.code for h in score.negative_hits}


def test_unknown_contract_in_snippet_requires_review_only_when_employment_is_a_constraint():
    canonical = group(body="Führerschein Klasse B erforderlich. Keine Deutschkenntnisse erforderlich.")
    signals, verdict, score = evaluate(canonical)
    assert not signals.employed_contract_signal
    assert not verdict.hard_reject
    assert score.score == 69
    assert evaluate(canonical, replace(PROFILE, self_employment_ok=None))[2].score >= 70


def test_structured_permanent_contract_is_evidence_even_with_a_snippet():
    canonical = group(body="Klasse B erforderlich. Kein Deutsch erforderlich.")
    record = replace(canonical.source_records[0], raw_payload={"contract_type": "permanent"})
    signals, verdict, score = evaluate(replace(canonical, source_records=(record,)))
    assert signals.employed_contract_signal
    assert not verdict.hard_reject
    assert score.score >= 70


@pytest.mark.parametrize("age", [3, 14, 30, 60, 90, 180, 365])
def test_freshness_boundaries_are_monotone(age):
    def weight(days):
        return sum(h.weight for h in quality_differentiator_hits(posted_date=TODAY-timedelta(days=days), vacancy_text="", today=TODAY))
    assert weight(age-1) >= weight(age) > weight(age+1)


def test_year_old_match_cannot_compete_with_fresh_or_be_hot():
    canonical = group(body="Klasse B erforderlich. Kein Deutsch erforderlich. Vollzeit, Quereinsteiger willkommen.")
    fresh = evaluate(canonical)[2]
    old = evaluate(replace(canonical, posted_date=TODAY-timedelta(days=369)))[2]
    assert fresh.score - old.score >= 30
    assert old.score < 70
    assert not quality_differentiator_hits(posted_date=None, vacancy_text="", today=TODAY)


@pytest.mark.parametrize("age,capped", [(180, False), (181, True), (365, True), (366, True)])
def test_stale_hot_cap_boundary(age, capped):
    canonical = group(body="Klasse B erforderlich. Kein Deutsch erforderlich. Vollzeit, Quereinsteiger willkommen.")
    score = evaluate(replace(canonical, posted_date=TODAY-timedelta(days=age)))[2]
    assert ("stale_posting_review" in {h.code for h in score.negative_hits}) is capped
    if capped:
        assert score.score < 70


def test_jooble_default_targets_german_domain_and_explicit_override_wins(monkeypatch):
    monkeypatch.delenv("SOURCE_JOOBLE_BASE_URL", raising=False)
    assert Settings().source_jooble_base_url == "https://de.jooble.org/api"
    monkeypatch.setenv("SOURCE_JOOBLE_BASE_URL", "https://jooble.org/api")
    assert Settings().source_jooble_base_url == "https://jooble.org/api"


@pytest.mark.parametrize("reverse", [False, True])
def test_schwerin_pair_without_shared_reference_or_ba_body_is_not_forced_to_merge(reverse):
    # Same title/city and an apparent branch suffix are not independent identity
    # proof: Adzuna is dated June 5, BA July 7 and has no description in the payload.
    records = (live_record("5752620136"), live_record("12913-7767db3af6b9a98f-S"))
    assert records[0].posted_date == date(2026, 6, 5)
    assert records[1].posted_date == date(2026, 7, 7)
    assert not records[1].body_text
    assert records[1].external_id not in records[0].body_text
    groups = SourceMergeService().merge_records(records[::-1] if reverse else records).canonical_groups
    assert len(groups) == 2
    assert {record.source_url for group in groups for record in group.source_records} == {
        record.source_url for record in records
    }
