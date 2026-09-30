"""Разбор формы профиля.

Главное, что здесь проверяется: «не указано» не подменяется значением по
умолчанию, а отсутствие поля в форме отличается от пустого поля. От этого
различия зависят жёсткие фильтры поиска — правило «человек сказал нет» скрывает
вакансию, а «человек не сказал ничего» только помечает её риском.
"""
from __future__ import annotations

import pytest
from app.services.employment_signal_extractor import (
    EMPLOYMENT_FULL_TIME,
    EMPLOYMENT_MINI_JOB,
    EMPLOYMENT_PART_TIME,
)
from app.services.profile_form_spec import MAX_PROFILE_RADIUS_KM, parse_profile_form


def test_primary_and_additional_roles_merge_in_order() -> None:
    fields = parse_profile_form(
        {"primary_role": "Перегон автомобилей", "additional_roles": "Fahrzeuglogistik, Werkstattfahrer"}
    )
    assert fields.desired_roles == ("Перегон автомобилей", "Fahrzeuglogistik", "Werkstattfahrer")


def test_primary_city_comes_first_and_duplicates_drop() -> None:
    fields = parse_profile_form({"primary_city": "Neustrelitz", "additional_cities": "Berlin, neustrelitz"})
    assert fields.preferred_locations == ("Neustrelitz", "Berlin")


def test_empty_tristate_stays_unspecified() -> None:
    """Пустой select — это «не указано», а не «нет»."""
    fields = parse_profile_form({"self_employment_ok": "", "physical_work_ok": "", "relocation_ready": ""})
    assert fields.self_employment_ok is None
    assert fields.physical_work_ok is None
    assert fields.relocation_ready is None


@pytest.mark.parametrize("raw,expected", [("true", True), ("false", False), ("да", True), ("нет", False)])
def test_tristate_reads_yes_and_no(raw: str, expected: bool) -> None:
    assert parse_profile_form({"self_employment_ok": raw}).self_employment_ok is expected


def test_absent_field_is_not_submitted_but_empty_field_is() -> None:
    """Обновление обязано различать «поля не было» и «поле пришло пустым»."""
    absent = parse_profile_form({"name": "X"})
    present_empty = parse_profile_form({"name": "X", "excluded_roles": ""})

    assert absent.was_submitted("excluded_roles") is False
    assert present_empty.was_submitted("excluded_roles") is True
    assert present_empty.excluded_roles == ()


def test_checkbox_marker_makes_unchecked_state_submittable() -> None:
    """Снятый чекбокс браузер не присылает — его присутствие несёт скрытый маркер."""
    unchecked = parse_profile_form({"no_german_required_present": "1"})
    checked = parse_profile_form({"no_german_required_present": "1", "no_german_required": "on"})

    assert unchecked.was_submitted("no_german_required") is True
    assert unchecked.no_german_required is False
    assert checked.no_german_required is True


def test_radius_is_clamped_and_zero_is_kept() -> None:
    """Ноль — осмысленный выбор «строго эти города», а не «не указано»."""
    assert parse_profile_form({"search_radius_km": "0"}).search_radius_km == 0
    assert parse_profile_form({"search_radius_km": "999"}).search_radius_km == MAX_PROFILE_RADIUS_KM
    assert parse_profile_form({"search_radius_km": ""}).search_radius_km is None
    assert parse_profile_form({"search_radius_km": "не число"}).search_radius_km is None


def test_salary_accepts_comma_decimal_and_rejects_zero() -> None:
    assert parse_profile_form({"min_salary_eur_per_hour": "16,5"}).min_salary_eur_per_hour == 16.5
    # Ноль как ориентир по оплате смысла не несёт — это «не указано».
    assert parse_profile_form({"min_salary_eur_per_hour": "0"}).min_salary_eur_per_hour is None


def test_employment_types_keep_fixed_order() -> None:
    """Порядок фиксированный, чтобы один и тот же набор читался одинаково."""
    fields = parse_profile_form({"employment_types": [EMPLOYMENT_MINI_JOB, EMPLOYMENT_FULL_TIME]})
    assert fields.employment_types == (EMPLOYMENT_FULL_TIME, EMPLOYMENT_MINI_JOB)


def test_employment_types_ignore_unknown_values() -> None:
    fields = parse_profile_form({"employment_types": [EMPLOYMENT_PART_TIME, "не существует"]})
    assert fields.employment_types == (EMPLOYMENT_PART_TIME,)


def test_driver_license_accepts_only_known_categories() -> None:
    """По этому полю работает жёсткий фильтр — опечатка меняла бы выдачу молча."""
    assert parse_profile_form({"driver_license": "b"}).driver_license == "B"
    assert parse_profile_form({"driver_license": "CE"}).driver_license == "CE"
    assert parse_profile_form({"driver_license": "категория Б"}).driver_license is None


def test_search_terms_are_split_and_deduplicated() -> None:
    fields = parse_profile_form(
        {"search_query_terms": "Fahrzeugüberführer, Überführungsfahrer; fahrzeugüberführer"}
    )
    assert fields.search_query_terms == ("Fahrzeugüberführer", "Überführungsfahrer")
