"""Проблема 4: одно агентство не забивает «горячие»."""
from __future__ import annotations

from datetime import datetime

from app.core.relevance_config import RelevanceConfig
from app.services.employer_clusters import collapse_employer_clusters
from app.services.search_export_service import build_search_export
from app.services.search_models import FilterResult, ScoreResult, SearchResultItem, VacancySignalSnapshot
from tests.services.relevance_support import build_canonical, courier_run, flatten_cards

_CONFIG = RelevanceConfig(employer_cluster_min_size=5, max_cards_per_employer_in_top=3)


def _item(index: int, company: str, *, bucket: str = "hot", body: str = "Auslieferung in Berlin.") -> SearchResultItem:
    canonical = build_canonical(title=f"Fahrer {index}", body=body, company=company, external_id=f"{company}-{index}")
    return SearchResultItem(
        canonical_group=canonical,
        primary_record=canonical.source_records[0],
        signals=VacancySignalSnapshot(combined_text=""),
        filter_result=FilterResult(decision="allow"),
        score_result=ScoreResult(score=90 - index),
        bucket=bucket,
        explanation_ru="",
    )


def test_cluster_above_limit_becomes_one_summary_card() -> None:
    agency = [_item(index, "PerZukunft Arbeitsvermittlung GmbH & Co. KG") for index in range(7)]
    other = [_item(10, "Picnic GmbH")]

    result = collapse_employer_clusters([*agency, *other], config=_CONFIG)

    hot = [item for item in result if item.bucket == "hot"]
    assert len(hot) == 2
    summary = hot[0]
    assert summary.cluster_size == 7
    assert len(summary.cluster_members) == 6
    assert all(member.collapsed_into == summary.canonical_group.canonical_key for member in summary.cluster_members)


def test_spelling_variants_and_shared_contact_join_one_cluster() -> None:
    contact = "Bewerbung an jobs@muster-personal.de oder +49 30 1234567."
    items = [
        _item(0, "perZukunft"),
        _item(1, "PerZukunft Arbeitsvermittlung GmbH&Co.KG"),
        _item(2, "Muster Personal", body=contact),
        _item(3, "Muster Personal Service GmbH", body=contact),
        _item(4, "PerZukunft Arbeitsvermittlung GmbH & Co. KG", body=contact),
        _item(5, "perZukunft"),
    ]

    result = collapse_employer_clusters(items, config=_CONFIG)

    assert len(result) == 1
    assert "jobs@muster-personal.de" in result[0].cluster_contacts


def test_small_cluster_is_capped_per_employer_in_top() -> None:
    items = [_item(index, "Deutsche Post AG" if index % 2 else "DHL") for index in range(5)]

    result = collapse_employer_clusters(items, config=_CONFIG)

    assert len(result) == 3
    assert len(result[0].cluster_members) == 2


def test_maybe_and_rejected_are_not_collapsed() -> None:
    items = [_item(index, "perZukunft", bucket="maybe") for index in range(8)]

    assert len(collapse_employer_clusters(items, config=_CONFIG)) == 8


def test_export_keeps_collapsed_vacancies_and_marks_them() -> None:
    result = courier_run()
    export = build_search_export(result, generated_at=datetime(2026, 10, 7, 12))

    collapsed = [vacancy for vacancy in export["vacancies"] if vacancy["collapsed_into"]]
    summaries = [vacancy for vacancy in export["vacancies"] if vacancy["employer_cluster"]]
    assert len(export["vacancies"]) == len(flatten_cards((*result.hot_results, *result.maybe_results, *result.rejected_results)))
    assert export["run"]["totals"]["collapsed_into_employer_cards"] == len(collapsed)
    assert all(summary["employer_cluster"]["member_keys"] for summary in summaries)


def test_real_run_top_has_at_most_three_cards_per_employer() -> None:
    from app.services.employer_clusters import item_employer_key

    hot = courier_run().hot_results
    counts: dict[str, int] = {}
    for item in hot:
        key = item_employer_key(item)
        counts[key] = counts.get(key, 0) + 1

    assert max(counts.values(), default=0) <= _CONFIG.max_cards_per_employer_in_top
