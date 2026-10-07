"""Проблема 8: штраф по обратной связи точечный, ограниченный и отключаемый."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.relevance_memory_service import RelevanceMemoryService
from app.services.scorer import has_strong_positive_signals
from app.services.search_models import VacancySignalSnapshot
from tests.services.relevance_support import courier_fixture, courier_run, items_titled, visible_items


def _memory(rows: list[tuple[str, str, str]]):
    return RelevanceMemoryService().build_memory_from_rows(
        [
            SimpleNamespace(canonical_key=f"k{index}", source_name=source, normalized_title=title,
                            role_family="driving", feedback_label=label)
            for index, (title, label, source) in enumerate(rows)
        ],
        profile_id=1,
    )


_DRIVING = frozenset({"driving"})


def test_penalty_uses_title_similarity_not_whole_family() -> None:
    memory = _memory([("lkw fahrer", "irrelevant", "BA")] * 5)
    service = RelevanceMemoryService()

    unrelated = service.compute_score_adjustment(
        memory, normalized_title="paketzusteller in vollzeit", role_family="driving", source_name="Careerjet",
        target_families=_DRIVING,
    )
    similar = service.compute_score_adjustment(
        memory, normalized_title="lkw fahrer nahverkehr", role_family="driving", source_name="Careerjet",
        target_families=_DRIVING,
    )

    assert unrelated.adjustment == 0
    assert similar.adjustment < 0


def test_off_target_family_is_still_penalised_as_a_family() -> None:
    memory = RelevanceMemoryService().build_memory_from_rows(
        [SimpleNamespace(canonical_key=f"k{i}", source_name="BA", normalized_title=f"softwareentwickler {i}",
                         role_family="it", feedback_label="irrelevant") for i in range(3)],
        profile_id=1,
    )

    result = RelevanceMemoryService().compute_score_adjustment(
        memory, normalized_title="it support", role_family="it", source_name="BA", target_families=_DRIVING,
    )

    assert result.adjustment < 0


def test_penalty_is_capped_and_source_penalty_is_small_and_separate() -> None:
    rows = [("lkw fahrer", "irrelevant", "Adzuna")] * 20
    memory = _memory(rows)

    result = RelevanceMemoryService().compute_score_adjustment(
        memory, normalized_title="lkw fahrer", role_family="driving", source_name="Adzuna", target_families=_DRIVING,
    )

    assert result.adjustment >= -10
    assert result.source_adjustment == -2


def test_strong_positive_signals_suppress_the_penalty() -> None:
    memory = _memory([("paketzusteller", "irrelevant", "Adzuna")] * 4)

    result = RelevanceMemoryService().compute_score_adjustment(
        memory, normalized_title="paketzusteller", role_family="driving", source_name="Adzuna",
        target_families=_DRIVING, strong_positive=True,
    )

    assert result.adjustment == 0
    assert result.penalty_suppressed


def test_strong_positive_needs_two_of_rate_full_time_and_class_b() -> None:
    base = VacancySignalSnapshot(combined_text="")
    rate = {"salary_is_comparable": True, "salary_hourly_eur": 17.92}

    assert not has_strong_positive_signals(VacancySignalSnapshot(combined_text="", **rate))
    assert has_strong_positive_signals(VacancySignalSnapshot(combined_text="", employment_types=("full_time",), **rate))
    assert has_strong_positive_signals(
        VacancySignalSnapshot(combined_text="", employment_types=("full_time",), required_driver_license_categories=("B",))
    )
    assert not has_strong_positive_signals(base)


def test_real_run_full_time_deutsche_post_has_no_feedback_penalty() -> None:
    assert courier_fixture()["feedback"], "фикстура должна содержать реальную обратную связь"
    cards = [
        item for item in items_titled(visible_items(courier_run()), "Paketzusteller")
        if "full_time" in item.signals.employment_types
        and "post" in (item.primary_record.original_company or "").lower()
    ]

    assert cards
    for item in cards:
        assert not {hit.code for hit in item.score_result.negative_hits} & {"feedback_penalty", "feedback_source_penalty"}
