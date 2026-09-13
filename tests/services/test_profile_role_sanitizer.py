from __future__ import annotations

from app.services.profile_role_sanitizer import (
    drop_excluded_roles_contradicting_desired,
    looks_like_role,
    sanitize_role_list,
)


def test_real_excluded_roles_garbage_is_reduced_to_the_actual_exclusions() -> None:
    """Реальный разбор профиля «Доставка / Курьер» из выгрузки 2026-09-13.

    LLM разрезал фразу по запятым, и в жёсткий фильтр попали куски предложения.
    """
    raw = [
        "амазон",
        "хермес",
        "также в доставку воды",
        "но без знания немецкого",
        "английского",
        "Amazon",
        "Hermes",
    ]

    assert sanitize_role_list(raw) == ["амазон", "хермес", "Amazon", "Hermes"]


def test_sentence_fragments_are_not_roles() -> None:
    for fragment in (
        "но без знания немецкого",
        "а также курьером",
        "также в доставку воды",
        "и не Amazon",
        "или что-то похожее",
        "можно склад",
    ):
        assert looks_like_role(fragment) is False, fragment


def test_language_mentions_are_dropped_but_language_jobs_are_kept() -> None:
    assert looks_like_role("английского") is False
    assert looks_like_role("deutsch") is False
    assert looks_like_role("знание английского") is False
    assert looks_like_role("уровень немецкого") is False
    # Профессия, в которой язык — корень слова, а не требование.
    assert looks_like_role("Deutschlehrer") is True
    assert looks_like_role("Учитель немецкого") is True
    assert looks_like_role("Englischlehrer") is True


def test_too_generic_and_empty_entries_are_dropped() -> None:
    assert sanitize_role_list(["работа", "job", "", "   ", "3,5", "ok"]) == []


def test_real_roles_survive_unchanged_with_original_casing() -> None:
    roles = ["Lagerarbeiter", "Kommissionierer", "Amazon", "Курьер"]
    assert sanitize_role_list(roles) == roles


def test_duplicates_are_collapsed_case_insensitively() -> None:
    assert sanitize_role_list(["Amazon", "amazon", "AMAZON"]) == ["Amazon"]


def test_exclusion_contradicting_a_desired_role_is_dropped() -> None:
    kept = drop_excluded_roles_contradicting_desired(
        desired_roles=["Доставка", "Курьер", "доставка воды"],
        excluded_roles=["в доставку воды", "Amazon"],
    )
    assert kept == ["Amazon"]


def test_exclusions_survive_when_nothing_contradicts_them() -> None:
    kept = drop_excluded_roles_contradicting_desired(
        desired_roles=["Lagerarbeiter"],
        excluded_roles=["Pflege", "Kraftfahrer"],
    )
    assert kept == ["Pflege", "Kraftfahrer"]


def test_no_desired_roles_leaves_exclusions_untouched() -> None:
    assert drop_excluded_roles_contradicting_desired(
        desired_roles=[], excluded_roles=["Amazon"]
    ) == ["Amazon"]


def test_none_and_non_string_entries_are_ignored() -> None:
    assert sanitize_role_list(None) == []
    assert sanitize_role_list(["Курьер", None, 42, "Amazon"]) == ["Курьер", "Amazon"]  # type: ignore[list-item]
