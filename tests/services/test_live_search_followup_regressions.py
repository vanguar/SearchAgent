"""Narrow regressions reported after the September live search quality fixes."""
import json
from dataclasses import replace
from itertools import permutations
from unittest.mock import MagicMock

import pytest
from app.services.normalizer import VacancyNormalizer
from app.services.rule_catalog import inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_export_service import _signals
from app.services.search_models import SearchProfileContext
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService
from app.services.summary_service import SummaryService

PAIRS = (
    ("12016-10005358982-S", "Fahrzeugüberführer m/w/d hochwertigen Pkw's"),
    ("12016-10005373885-S", "Überführer / Fahrer (m/w/d) Berliner Raum"),
)
PROFILE = SearchProfileContext(
    profile_label="Перегон", profile_source="saved", desired_roles=("Fahrzeugüberführer",),
    driver_license="B", min_salary_eur_per_hour=20,
)


def record(source, external_id, title, body="", *, company="perZukunft", city="Berlin", salary=None):
    return VacancyNormalizer().normalize_source_record(SourceRecordPreview(
        source_id=source, source_name=source, external_id=external_id, source_reference=None,
        title=title, company=company, location=city,
        posted_at="2026-09-01" if source == "ba" else "2026-10-01",
        detail_url=f"https://example.org/{source}/{external_id}",
        raw_payload={"description": body, "salary": salary},
    ))


@pytest.mark.parametrize("reference,title", PAIRS)
@pytest.mark.parametrize("order", tuple(permutations(range(3))))
def test_live_ba_external_reference_merges_all_sources_in_any_order(reference, title, order):
    records = (
        record("ba", reference, title, company="PerZukunft Arbeitsvermittlung GmbH & Co. KG"),
        record("adzuna", "aggregator-a", title, f"Referenznummer: {reference}. Klasse B erforderlich."),
        record("careerjet", "aggregator-c", title, f"Fahrzeugüberführung. Referenznummer: {reference}"),
    )
    merged = SourceMergeService().merge_records(tuple(records[index] for index in order))
    assert len(merged.canonical_groups) == 1
    canonical = merged.canonical_groups[0]
    assert {r.source_url for r in canonical.source_records} == {r.source_url for r in records}
    assert len(canonical.provenance) == 3
    assert any("ba_employer_reference_match" in decision.reason_codes for decision in merged.merge_decisions)


@pytest.mark.parametrize("difference", ["reference", "employer", "city", "role", "non_ba_external_id", "unlabelled_id"])
@pytest.mark.parametrize("reverse", [False, True])
def test_reference_identity_does_not_merge_unrelated_jobs(difference, reverse):
    reference, title = PAIRS[0]
    ba = record("ba", reference, title, company="PerZukunft Arbeitsvermittlung GmbH & Co. KG")
    other = record("adzuna", "aggregator", title, f"Referenznummer: {reference}")
    if difference == "reference":
        other = replace(other, body_text="Referenznummer: 12016-10005373885-S")
    elif difference == "employer":
        other = replace(other, normalized_company="another employer")
    elif difference == "city":
        other = record("adzuna", "aggregator", title, f"Referenznummer: {reference}", city="Rostock")
    elif difference == "role":
        other = record("adzuna", "aggregator", "Softwareentwickler Python", f"Referenznummer: {reference}")
    elif difference == "non_ba_external_id":
        ba = replace(ba, source_id="unrelated_board")
    else:
        other = replace(other, body_text=f"Weitere Angebote: {reference}")
    records = (ba, other)
    assert len(SourceMergeService().merge_records(records[::-1] if reverse else records).canonical_groups) == 2


def test_different_ba_reference_blocks_old_fuzzy_match_with_identical_company():
    reference, title = PAIRS[0]
    records = (
        record("ba", reference, title),
        record("adzuna", "other", title, "Referenznummer: 12016-10005373885-S"),
    )
    assert len(SourceMergeService().merge_records(records).canonical_groups) == 2


@pytest.mark.parametrize("use_llm", [False, True])
def test_live_salary_conflict_has_same_calculation_and_summary_with_provenance(use_llm):
    title = "Auslieferungsfahrer / Überführer m/w/d 18,23 € / Stunde"
    original = record("careerjet", "salary-conflict", title, "Wir bieten 25,54 € / Stunde. Klasse B erforderlich.")
    canonical = SourceMergeService().merge_records((original,)).canonical_groups[0]
    signals = inspect_vacancy(canonical, PROFILE)
    score = VacancyScorer().score(canonical, PROFILE, signals=signals)
    helper = MagicMock()
    helper.summarize.return_value = "Оплата 25,54 €/ч."
    service = SummaryService(helper=helper)
    service._llm_cache[original.body_text] = "Оплата 25,54 €/ч."
    summary = service.build_summary(canonical, signals, use_llm=use_llm)
    assert signals.salary_hourly_eur == 18.23
    assert signals.salary_conflict
    assert "salary_below_target" in {hit.code for hit in score.negative_hits}
    assert "salary_above_target" not in {hit.code for hit in score.positive_hits}
    assert "заголовок: 18,23 €/ч" in summary
    assert "описание: 25,54 €/ч" in summary
    assert "Расчётная ставка для оценки: 18,23 €/ч" in summary
    assert "требует уточнения" in summary
    helper.summarize.assert_not_called()
    exported = _signals(signals, full=True)
    assert exported["salary_conflict"] is True
    assert {e["field"]: e["hourly_eur"] for e in exported["salary_evidence"]} == {"title": 18.23, "body": 25.54}
    assert all(e["source_url"] == original.source_url for e in exported["salary_evidence"])
    assert json.loads(json.dumps(exported))["salary_evidence"] == exported["salary_evidence"]
    assert canonical.source_records[0] == original


@pytest.mark.parametrize("title_rate,body_rate,metadata_rate,conflict", [
    ("25,54", "18,23", "30,00", True),
    ("18,23", "18,23", "30,00", True),
    ("18,23", "18,23", "12,00", True),
    ("18,23", "18,23", "18,23", False),
])
def test_metadata_is_provenance_not_an_implicit_salary_override(title_rate, body_rate, metadata_rate, conflict):
    original = record("adzuna", "salary-fields", f"Überführer {title_rate} € / Stunde",
                      f"Wir bieten {body_rate} € / Stunde.", salary=f"{metadata_rate} € / Stunde")
    canonical = SourceMergeService().merge_records((original,)).canonical_groups[0]
    signals = inspect_vacancy(canonical, PROFILE)
    assert signals.salary_hourly_eur == 18.23
    assert signals.salary_conflict is conflict
    assert {item.field for item in signals.salary_evidence} == {"title", "body", "metadata.salary"}
    if conflict:
        summary = SummaryService().build_summary(canonical, signals)
        assert "Расчётная ставка для оценки: 18,23 €/ч" in summary


def test_equivalent_hourly_and_monthly_amounts_are_not_a_conflict():
    original = record("careerjet", "salary-equivalent", "Überführer 20 € / Stunde", "3460 € pro Monat brutto")
    canonical = SourceMergeService().merge_records((original,)).canonical_groups[0]
    assert not inspect_vacancy(canonical, PROFILE).salary_conflict
