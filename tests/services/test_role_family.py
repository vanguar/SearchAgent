"""Deterministic tests for role-family classification and compatibility."""
from __future__ import annotations

import pytest
from app.services.role_family import (
    RoleFamily,
    classify_desired_roles,
    classify_query_ru,
    classify_role_text,
    classify_vacancy_de,
    families_are_compatible,
    is_specific_family,
    prohibits_broad_fallback,
)

# ---------------------------------------------------------------------------
# classify_query_ru — Russian query/role classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("it", RoleFamily.IT),
    ("IT", RoleFamily.IT),          # case-insensitive via .lower() in caller
    ("  it  ", RoleFamily.IT),
    ("программист", RoleFamily.IT),
    ("программирование", RoleFamily.IT),
    ("python developer", RoleFamily.IT),
    ("devops инженер", RoleFamily.IT),
    ("техподдержка", RoleFamily.IT),
    ("автоматизация тестирования", RoleFamily.IT),
])
def test_classify_query_ru_it(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    # "it" as a substring inside longer words must NOT match IT
    ("quality control", RoleFamily.GENERIC),   # "it" inside "quality"
    ("transit arbeit", RoleFamily.GENERIC),    # "it" inside "transit"
    ("unitarbeiter", RoleFamily.GENERIC),      # "it" inside compound
    # "it" as a standalone word DOES match
    ("it security", RoleFamily.IT),
    ("работаю в it", RoleFamily.IT),
])
def test_classify_query_ru_it_word_boundary_safe(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("склад", RoleFamily.WAREHOUSE),
    ("складская работа", RoleFamily.WAREHOUSE),
    ("логистика", RoleFamily.WAREHOUSE),
    ("комплектовщик", RoleFamily.WAREHOUSE),
    ("кладовщик", RoleFamily.WAREHOUSE),
    ("грузчик", RoleFamily.WAREHOUSE),
    ("упаковщик на склад", RoleFamily.WAREHOUSE),
    ("упаковка", RoleFamily.WAREHOUSE),
])
def test_classify_query_ru_warehouse(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("производство", RoleFamily.PRODUCTION),
    ("производственный рабочий", RoleFamily.PRODUCTION),
    ("сборщик", RoleFamily.PRODUCTION),
])
def test_classify_query_ru_production(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("повар", RoleFamily.KITCHEN),
    ("кухонный работник", RoleFamily.KITCHEN),
    ("официант", RoleFamily.KITCHEN),
])
def test_classify_query_ru_kitchen(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("уборщик", RoleFamily.CLEANING),
    ("уборка помещений", RoleFamily.CLEANING),
])
def test_classify_query_ru_cleaning(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("медсестра", RoleFamily.HEALTHCARE),
    ("медицинский работник", RoleFamily.HEALTHCARE),
    ("санитар", RoleFamily.HEALTHCARE),
])
def test_classify_query_ru_healthcare(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("водитель", RoleFamily.DRIVING),
    ("курьер", RoleFamily.DRIVING),
])
def test_classify_query_ru_driving(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("", RoleFamily.GENERIC),
    ("   ", RoleFamily.GENERIC),
    ("рабочий помощник", RoleFamily.GENERIC),
    ("неизвестная профессия", RoleFamily.GENERIC),
])
def test_classify_query_ru_generic_fallback(text: str, expected: RoleFamily) -> None:
    assert classify_query_ru(text) == expected


# ---------------------------------------------------------------------------
# classify_vacancy_de — German normalized title classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("softwareentwickler", RoleFamily.IT),
    ("softwareentwickler m w d", RoleFamily.IT),
    ("python developer", RoleFamily.IT),
    ("backend entwickler", RoleFamily.IT),
    ("it support specialist", RoleFamily.IT),
    ("devops engineer", RoleFamily.IT),
    ("helpdesk mitarbeiter", RoleFamily.IT),
])
def test_classify_vacancy_de_it(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("lagermitarbeiter", RoleFamily.WAREHOUSE),
    ("lagerhelfer m w d", RoleFamily.WAREHOUSE),
    ("lagerist", RoleFamily.WAREHOUSE),
    ("kommissionierer", RoleFamily.WAREHOUSE),
])
def test_classify_vacancy_de_warehouse(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("produktionsmitarbeiter", RoleFamily.PRODUCTION),
    ("produktionshelfer m w d", RoleFamily.PRODUCTION),
    ("maschinenbediener", RoleFamily.PRODUCTION),
])
def test_classify_vacancy_de_production(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("kuchenhelfer", RoleFamily.KITCHEN),      # küche → kuche after normalization
    ("kuchenhilfe m w d", RoleFamily.KITCHEN),
    ("koch", RoleFamily.KITCHEN),
    ("kochin", RoleFamily.KITCHEN),            # köchin → kochin
    ("kellner", RoleFamily.KITCHEN),
    ("restaurant mitarbeiter", RoleFamily.KITCHEN),
])
def test_classify_vacancy_de_kitchen(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("reinigungskraft", RoleFamily.CLEANING),
    ("reinigung mitarbeiter", RoleFamily.CLEANING),
    ("housekeeping", RoleFamily.CLEANING),
])
def test_classify_vacancy_de_cleaning(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("pflegekraft", RoleFamily.HEALTHCARE),
    ("pflegehelferin", RoleFamily.HEALTHCARE),
    ("altenpflege mitarbeiter", RoleFamily.HEALTHCARE),
    ("krankenschwester", RoleFamily.HEALTHCARE),
])
def test_classify_vacancy_de_healthcare(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("buchhalter", RoleFamily.OFFICE),
    ("sachbearbeiter buchhaltung", RoleFamily.OFFICE),
    ("verwaltungsmitarbeiter", RoleFamily.OFFICE),
])
def test_classify_vacancy_de_office(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("fahrer", RoleFamily.DRIVING),
    ("kraftfahrer", RoleFamily.DRIVING),
    ("kurier fahrer", RoleFamily.DRIVING),
    ("zusteller m w d", RoleFamily.DRIVING),
])
def test_classify_vacancy_de_driving(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("helfer", RoleFamily.GENERIC),
    ("mitarbeiter", RoleFamily.GENERIC),
    ("hilfskraft", RoleFamily.GENERIC),
    ("", RoleFamily.GENERIC),
    ("package manager", RoleFamily.GENERIC),   # IT English title without IT tokens
    ("allgemeine hilfstatigkeit", RoleFamily.GENERIC),
])
def test_classify_vacancy_de_generic(title: str, expected: RoleFamily) -> None:
    assert classify_vacancy_de(title) == expected


# ---------------------------------------------------------------------------
# is_specific_family
# ---------------------------------------------------------------------------

def test_is_specific_family_returns_true_for_specific_families() -> None:
    for family in RoleFamily:
        if family is RoleFamily.GENERIC:
            assert not is_specific_family(family)
        else:
            assert is_specific_family(family)


# ---------------------------------------------------------------------------
# families_are_compatible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,vacancy,expected", [
    # IT query is compatible only with IT and OFFICE vacancies (and GENERIC)
    (RoleFamily.IT, RoleFamily.IT, True),
    (RoleFamily.IT, RoleFamily.OFFICE, True),
    (RoleFamily.IT, RoleFamily.GENERIC, True),
    (RoleFamily.IT, RoleFamily.KITCHEN, False),
    (RoleFamily.IT, RoleFamily.CLEANING, False),
    (RoleFamily.IT, RoleFamily.AGRICULTURE, False),
    (RoleFamily.IT, RoleFamily.HEALTHCARE, False),
    (RoleFamily.IT, RoleFamily.WAREHOUSE, False),
    (RoleFamily.IT, RoleFamily.PRODUCTION, False),
    # WAREHOUSE query accepts warehouse, production, generic; driving stays separate.
    (RoleFamily.WAREHOUSE, RoleFamily.WAREHOUSE, True),
    (RoleFamily.WAREHOUSE, RoleFamily.PRODUCTION, True),
    (RoleFamily.WAREHOUSE, RoleFamily.DRIVING, False),
    (RoleFamily.WAREHOUSE, RoleFamily.GENERIC, True),
    (RoleFamily.WAREHOUSE, RoleFamily.KITCHEN, False),
    (RoleFamily.WAREHOUSE, RoleFamily.IT, False),
    (RoleFamily.WAREHOUSE, RoleFamily.HEALTHCARE, False),
    # PRODUCTION accepts production, warehouse, construction, generic
    (RoleFamily.PRODUCTION, RoleFamily.PRODUCTION, True),
    (RoleFamily.PRODUCTION, RoleFamily.WAREHOUSE, True),
    (RoleFamily.PRODUCTION, RoleFamily.CONSTRUCTION, True),
    (RoleFamily.PRODUCTION, RoleFamily.KITCHEN, False),
    (RoleFamily.PRODUCTION, RoleFamily.IT, False),
    # HEALTHCARE accepts only healthcare and generic
    (RoleFamily.HEALTHCARE, RoleFamily.HEALTHCARE, True),
    (RoleFamily.HEALTHCARE, RoleFamily.GENERIC, True),
    (RoleFamily.HEALTHCARE, RoleFamily.WAREHOUSE, False),
    (RoleFamily.HEALTHCARE, RoleFamily.KITCHEN, False),
    # GENERIC query accepts everything
    (RoleFamily.GENERIC, RoleFamily.IT, True),
    (RoleFamily.GENERIC, RoleFamily.KITCHEN, True),
    (RoleFamily.GENERIC, RoleFamily.HEALTHCARE, True),
])
def test_families_are_compatible(query: RoleFamily, vacancy: RoleFamily, expected: bool) -> None:
    assert families_are_compatible(query, vacancy) == expected


# ---------------------------------------------------------------------------
# classify_desired_roles
# ---------------------------------------------------------------------------

def test_classify_desired_roles_it_profile() -> None:
    families = classify_desired_roles(("it", "программист"))
    assert RoleFamily.IT in families


def test_classify_desired_roles_warehouse_profile() -> None:
    families = classify_desired_roles(("склад", "логистика", "упаковка"))
    assert RoleFamily.WAREHOUSE in families


def test_classify_desired_roles_mixed_profile() -> None:
    families = classify_desired_roles(("склад", "производство"))
    assert RoleFamily.WAREHOUSE in families
    assert RoleFamily.PRODUCTION in families


def test_classify_desired_roles_empty() -> None:
    assert classify_desired_roles(()) == frozenset()


def test_classify_desired_roles_unknown_role_maps_to_generic() -> None:
    families = classify_desired_roles(("неизвестная профессия",))
    assert RoleFamily.GENERIC in families


# ---------------------------------------------------------------------------
# prohibits_broad_fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family,expected", [
    (RoleFamily.IT, True),
    (RoleFamily.HEALTHCARE, True),
    (RoleFamily.OFFICE, True),
    (RoleFamily.SALES, True),
    (RoleFamily.SECURITY, True),
    (RoleFamily.KITCHEN, True),
    (RoleFamily.WAREHOUSE, False),
    (RoleFamily.PRODUCTION, False),
    (RoleFamily.CLEANING, False),
    (RoleFamily.CONSTRUCTION, False),
    (RoleFamily.AGRICULTURE, False),
    (RoleFamily.DRIVING, True),   # delivery stays in its own family, not generic labor
    (RoleFamily.GENERIC, False),
])
def test_prohibits_broad_fallback(family: RoleFamily, expected: bool) -> None:
    assert prohibits_broad_fallback(family) == expected


# ---------------------------------------------------------------------------
# Integration: mismatch scenarios mirroring real filter-engine use cases
# ---------------------------------------------------------------------------

def test_it_query_rejects_kitchen_vacancy() -> None:
    query_families = classify_desired_roles(("it", "программист"))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("kuchenhelfer m w d")
    assert is_specific_family(vacancy_family)
    assert not any(families_are_compatible(qf, vacancy_family) for qf in specific)


def test_it_query_rejects_cleaning_vacancy() -> None:
    query_families = classify_desired_roles(("it",))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("reinigungskraft")
    assert not any(families_are_compatible(qf, vacancy_family) for qf in specific)


def test_warehouse_query_accepts_production_vacancy() -> None:
    query_families = classify_desired_roles(("склад", "логистика"))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("produktionshelfer m w d")
    assert any(families_are_compatible(qf, vacancy_family) for qf in specific)


def test_warehouse_query_rejects_healthcare_vacancy() -> None:
    query_families = classify_desired_roles(("склад",))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("pflegekraft")
    assert not any(families_are_compatible(qf, vacancy_family) for qf in specific)


def test_warehouse_query_rejects_driving_vacancy() -> None:
    query_families = classify_desired_roles(("склад", "упаковка"))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("lieferfahrer m w d")
    assert vacancy_family is RoleFamily.DRIVING
    assert not any(families_are_compatible(qf, vacancy_family) for qf in specific)


def test_warehouse_query_accepts_generic_helper_vacancy() -> None:
    query_families = classify_desired_roles(("склад",))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("helfer")  # GENERIC
    assert specific  # warehouse query resolves to a specific family
    assert not is_specific_family(vacancy_family)  # generic vacancy → would NOT be mismatch-rejected


def test_accounting_query_rejects_warehouse_vacancy() -> None:
    query_families = classify_desired_roles(("бухгалтер",))
    specific = {f for f in query_families if is_specific_family(f)}
    vacancy_family = classify_vacancy_de("lagerhelfer")
    assert not any(families_are_compatible(qf, vacancy_family) for qf in specific)


def test_generic_profile_accepts_any_vacancy() -> None:
    # Profile with no desired roles → no specific families → no mismatch possible
    query_families = classify_desired_roles(())
    specific = {f for f in query_families if is_specific_family(f)}
    assert not specific  # empty → mismatch check returns False


# ---------------------------------------------------------------------------
# Symmetry invariant: key cross-compatible pairs must be mutually compatible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family_a,family_b", [
    (RoleFamily.IT, RoleFamily.OFFICE),
    (RoleFamily.WAREHOUSE, RoleFamily.PRODUCTION),
    (RoleFamily.PRODUCTION, RoleFamily.CONSTRUCTION),
    (RoleFamily.OFFICE, RoleFamily.SALES),
])
def test_families_are_compatible_key_pairs_are_symmetric(
    family_a: RoleFamily, family_b: RoleFamily
) -> None:
    assert families_are_compatible(family_a, family_b) == families_are_compatible(family_b, family_a)


def test_families_are_compatible_is_symmetric_by_construction() -> None:
    """All 13×13 = 169 pairs must be symmetric."""
    families = list(RoleFamily)
    for a in families:
        for b in families:
            assert families_are_compatible(a, b) == families_are_compatible(b, a), (
                f"asymmetry detected: families_are_compatible({a!r}, {b!r}) != "
                f"families_are_compatible({b!r}, {a!r})"
            )


def test_german_profile_roles_classify_into_their_family() -> None:
    """Роли профиля пишутся по-немецки, а классификатор понимал только русский.

    Из-за этого classify_desired_roles возвращала GENERIC, межсемейный фильтр
    отключался целиком, и складскому профилю прилетала автомастерская.
    """
    assert classify_desired_roles(("Lagerarbeiter", "Lagermitarbeiter")) == frozenset({RoleFamily.WAREHOUSE})
    assert classify_desired_roles(("Fahrer Klasse B",)) == frozenset({RoleFamily.DRIVING})


def test_english_profile_roles_classify_into_their_family() -> None:
    assert classify_desired_roles(("Driver B - Fernverkehr",)) == frozenset({RoleFamily.DRIVING})
    assert classify_desired_roles(("Warehouse Associate",)) == frozenset({RoleFamily.WAREHOUSE})


def test_russian_delivery_role_is_a_driving_family() -> None:
    assert classify_desired_roles(("Доставка", "Курьер")) == frozenset({RoleFamily.DRIVING})


def test_forklift_is_warehouse_work_not_road_driving() -> None:
    """"Staplerfahrer" кончается на "-fahrer", но работает на складе."""
    for title in ("Staplerfahrer", "Gabelstaplerfahrer", "Schubmaststaplerfahrer"):
        assert classify_role_text(title) is RoleFamily.WAREHOUSE, title


def test_cargo_domain_does_not_override_the_role_head_noun() -> None:
    """Водитель медизделий — водитель, а не медработник."""
    assert classify_role_text("Auslieferungsfahrer Medizinprodukte") is RoleFamily.DRIVING


def test_data_warehouse_is_not_a_warehouse_role() -> None:
    assert classify_role_text("Data Warehouse Engineer") is not RoleFamily.WAREHOUSE


def test_earliest_word_in_the_title_decides_the_family() -> None:
    """Немецкий заголовок начинается с главного слова роли и дополняет его справа.

    "Kommissionierer mit Fahrertätigkeiten" — складская вакансия с элементами
    вождения, а не водительская; раньше побеждало то семейство, что стояло выше
    в списке, и она попадала водительскому профилю.
    """
    assert classify_vacancy_de("kommissionierer mit fahrertatigkeiten") is RoleFamily.WAREHOUSE
    assert classify_vacancy_de("fahrer mit lagertatigkeiten") is RoleFamily.DRIVING
    assert classify_vacancy_de("helfer lagerwirtschaft transport") is RoleFamily.WAREHOUSE


def test_equal_position_falls_back_to_list_order() -> None:
    """В "Staplerfahrer" оба слова начинаются с нуля — решает порядок списка."""
    assert classify_vacancy_de("staplerfahrer") is RoleFamily.WAREHOUSE
