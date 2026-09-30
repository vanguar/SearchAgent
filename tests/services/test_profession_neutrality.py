"""Специализированные правила не должны улучшать одно направление за счёт других.

Правила автомобильной логистики, детекторы зарплаты, занятости и самозанятости
общие для всех профилей. Здесь проверяется, что IT, склад, уборка, кухня и
стройка работают ровно так же и ничего не потеряли.
"""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine, is_b_only_driving_profile
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.role_family import RoleFamily, classify_role_text, classify_vacancy_de
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import MAYBE_BUCKET_MIN_SCORE, inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_fallback import get_intent_fallback_keywords
from app.services.search_models import SearchProfileContext
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService


def _group(title: str, body: str, *, location: str = "10115 Berlin") -> CanonicalVacancyGroup:
    record = SourceRecordPreview(
        source_id="ba",
        source_name="BA",
        external_id=title[:32],
        source_reference=None,
        title=title,
        company="Beispiel GmbH",
        location=location,
        posted_at="2026-09-27",
        detail_url="https://example.org/job",
        raw_payload={"description": body},
    )
    return SourceMergeService().merge_records(
        (VacancyNormalizer().normalize_source_record(record),)
    ).canonical_groups[0]


def _evaluate(group: CanonicalVacancyGroup, profile: SearchProfileContext):
    signals = inspect_vacancy(group, profile)
    filter_result = FilterEngine().evaluate(group, profile, signals=signals, search_mode="germany_local")
    score = VacancyScorer().score(group, profile, signals=signals, filter_result=filter_result)
    return signals, filter_result, score


def _profile(**kwargs) -> SearchProfileContext:
    base = {
        "profile_label": "Тест",
        "profile_source": "saved",
        "german_level": "A2",
        "preferred_locations": ("Berlin",),
        "search_cities": ("Berlin",),
    }
    base.update(kwargs)
    return SearchProfileContext(**base)


# ---------------------------------------------------------------------------
# Запросы других профессий не съезжают в автомобильную логистику
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_family,expected_de",
    [
        ("склад", RoleFamily.WAREHOUSE, "lager"),
        ("комплектовщик", RoleFamily.WAREHOUSE, "kommissionierer"),
        ("курьер", RoleFamily.DRIVING, "kurier"),
        ("водитель", RoleFamily.DRIVING, "fahrer"),
        ("уборщик", RoleFamily.CLEANING, "reinigungskraft"),
        ("повар", RoleFamily.KITCHEN, "koch"),
        ("электрик", RoleFamily.CONSTRUCTION, "elektriker"),
        ("программист", RoleFamily.IT, "softwareentwickler"),
        ("производство", RoleFamily.PRODUCTION, "produktion"),
    ],
)
def test_existing_role_queries_are_unchanged(
    query: str, expected_family: RoleFamily, expected_de: str
) -> None:
    intent = normalize_role_intent(query)
    assert intent is not None
    assert intent.family is expected_family
    assert intent.primary_de == expected_de


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Lagermitarbeiter (m/w/d)", RoleFamily.WAREHOUSE),
        ("Kommissionierer", RoleFamily.WAREHOUSE),
        ("Staplerfahrer", RoleFamily.WAREHOUSE),
        ("Produktionshelfer", RoleFamily.PRODUCTION),
        ("Reinigungskraft", RoleFamily.CLEANING),
        ("Küchenhilfe", RoleFamily.KITCHEN),
        ("Elektriker", RoleFamily.CONSTRUCTION),
        ("Softwareentwickler Python", RoleFamily.IT),
        ("Lieferfahrer", RoleFamily.DRIVING),
        ("Paketzusteller", RoleFamily.DRIVING),
    ],
)
def test_existing_vacancy_titles_are_unchanged(title: str, expected: RoleFamily) -> None:
    from app.services.hashers import normalize_text_for_fingerprint

    assert classify_vacancy_de(normalize_text_for_fingerprint(title)) is expected


def test_warehouse_fallback_pool_is_unchanged() -> None:
    """Новое семейство не подмешалось в складской план расширения."""
    intent = normalize_role_intent("склад")
    assert intent is not None

    keywords = get_intent_fallback_keywords(
        intent=intent, primary_query=intent.primary_de, low_language=True
    )

    assert any("lager" in keyword.casefold() for keyword in keywords)
    forbidden = ("uberfuhr", "überführ", "rangier", "werkstattfahrer", "fahrzeuglogistik")
    for keyword in keywords:
        assert not any(token in keyword.casefold() for token in forbidden), keyword


def test_it_profile_does_not_receive_vehicle_logistics_keywords() -> None:
    intent = normalize_role_intent("python developer")
    assert intent is not None

    keywords = get_intent_fallback_keywords(
        intent=intent, primary_query=intent.primary_de, low_language=False
    )

    for keyword in keywords:
        assert "fahrzeug" not in keyword.casefold()
        assert "uberfuhr" not in keyword.casefold()


# ---------------------------------------------------------------------------
# Профили других профессий не становятся «водительскими»
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles",
    [("склад",), ("Python Developer",), ("уборщик",), ("повар",), ("электрик",)],
)
def test_non_driving_profiles_do_not_trigger_vehicle_class_filters(roles: tuple[str, ...]) -> None:
    """Отсечение тяжёлого транспорта относится только к работе за рулём."""
    assert is_b_only_driving_profile(_profile(desired_roles=roles, driver_license="B")) is False


def test_warehouse_vacancy_mentioning_a_truck_is_not_rejected() -> None:
    """Складская вакансия, где упомянут LKW, остаётся складской."""
    profile = _profile(desired_roles=("склад",), driver_license="B")
    group = _group(
        "Lagermitarbeiter (m/w/d)",
        "Kommissionierung und Verladung. Beladung von LKW am Wareneingang. "
        "Schichtarbeit, unbefristet, 14,50 € pro Stunde.",
    )

    _, filter_result, score = _evaluate(group, profile)

    assert filter_result.hard_reject is False
    assert score.score >= MAYBE_BUCKET_MIN_SCORE


# ---------------------------------------------------------------------------
# Новые детекторы одинаково работают на любой профессии
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles,title,body",
    [
        (("склад",), "Lagerhelfer (m/w/d)", "Lagerarbeit. 15,00 € pro Stunde brutto. Vollzeit, unbefristet."),
        (("уборщик",), "Reinigungskraft (m/w/d)", "Unterhaltsreinigung. 14,00 € pro Stunde. Teilzeit."),
        (("повар",), "Koch (m/w/d)", "Küche. 3.000 € monatlich brutto. Vollzeit."),
    ],
)
def test_salary_extraction_works_for_every_profession(
    roles: tuple[str, ...], title: str, body: str
) -> None:
    signals, _, _ = _evaluate(_group(title, body), _profile(desired_roles=roles))

    assert signals.salary_hourly_eur is not None
    assert signals.salary_is_comparable is True


def test_self_employment_is_detected_for_a_cleaning_profile() -> None:
    """Правило про самозанятость общее, а не для одного направления."""
    profile = _profile(desired_roles=("уборщик",), self_employment_ok=False)
    group = _group(
        "Reinigungskraft (m/w/d)",
        "Reinigung auf selbstständiger Basis, Gewerbeschein erforderlich.",
    )

    _, filter_result, _ = _evaluate(group, profile)

    assert filter_result.hard_reject is True
    assert "self_employment_mismatch" in {hit.code for hit in filter_result.rejection_hits}


def test_heavy_physical_work_is_detected_for_a_warehouse_profile() -> None:
    profile = _profile(desired_roles=("склад",), physical_work_ok=False)
    group = _group(
        "Lagerhelfer (m/w/d)",
        "Kommissionierung, körperlich anstrengende Arbeit, Heben und Tragen bis 25 kg.",
    )

    _, filter_result, _ = _evaluate(group, profile)

    assert filter_result.hard_reject is True
    assert "heavy_physical_mismatch" in {hit.code for hit in filter_result.rejection_hits}


def test_physical_work_allowed_keeps_the_vacancy() -> None:
    """Кто согласен на физическую работу, тот её и получает."""
    profile = _profile(desired_roles=("склад",), physical_work_ok=True)
    group = _group(
        "Lagerhelfer (m/w/d)",
        "Kommissionierung, körperlich anstrengende Arbeit, Heben bis 25 kg. "
        "Unbefristet, 15,00 € pro Stunde.",
    )

    _, filter_result, score = _evaluate(group, profile)

    assert filter_result.hard_reject is False
    assert "heavy_physical_unknown" not in {hit.code for hit in score.negative_hits}


def test_employment_type_review_works_for_an_it_profile() -> None:
    profile = _profile(desired_roles=("Python Developer",), employment_types=("full_time",))
    group = _group(
        "Python Entwickler (m/w/d)",
        "Python, FastAPI. Minijob auf 538-Euro-Basis. Remote möglich.",
    )

    _, filter_result, _ = _evaluate(group, profile)

    assert "employment_type_review" in {hit.code for hit in filter_result.review_hits}


# ---------------------------------------------------------------------------
# Профессии, не описанные словарём, по-прежнему не ломаются
# ---------------------------------------------------------------------------


def test_unknown_profession_yields_no_intent_and_no_generic_substitute() -> None:
    """Незнакомую роль нельзя молча подменять «helfer» — лучше ничего."""
    assert normalize_role_intent("quality control") is None
    assert classify_role_text("quality control") is RoleFamily.GENERIC


def test_compound_title_keeps_the_user_wording_and_finds_its_family() -> None:
    """Составное название сохраняется как запрос, а семейство берётся из словаря."""
    intent = normalize_role_intent("Maschinenschlosser")

    assert intent is not None
    assert intent.primary_de == "Maschinenschlosser"
    assert intent.canonical_keyword == "schlosser"
    assert intent.family is RoleFamily.CONSTRUCTION
