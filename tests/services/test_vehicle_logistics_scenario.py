"""Приёмочный сценарий: пользователь сам создал профиль на перегон автомобилей.

Это ПРОВЕРКА возможностей на одном примере, а не включение новой категории:
профиль здесь собирается во временной тестовой базе, и ни одного
предустановленного профиля в приложении не появляется.

Тот же набор проверок для IT и других профессий лежит в
test_profession_neutrality.py — специализированные правила не должны улучшать
одно направление за счёт остальных.
"""
from __future__ import annotations

import re

import pytest
from app.services.filter_engine import FilterEngine, is_b_only_driving_profile
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.role_family import RoleFamily, classify_role_text, classify_vacancy_de
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import HOT_BUCKET_MIN_SCORE, MAYBE_BUCKET_MIN_SCORE, inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_fallback import get_intent_fallback_keywords
from app.services.search_models import SearchProfileContext
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService

# Немецкие запросы из технического задания. Профиль с такими словами человек
# заводит сам; словарь обязан их понимать и не подменять общим «fahrer».
VEHICLE_QUERIES: tuple[str, ...] = (
    "Fahrzeugüberführer",
    "Überführungsfahrer",
    "Fahrzeugüberführung",
    "Fahrzeugverbringung",
    "Fahrzeugumsetzer",
    "Umsetzfahrer",
    "PKW-Rangierer",
    "Fahrer Fahrzeuglogistik",
    "Werkstattfahrer",
    "Fahrer Hol- und Bringservice",
    "Hol- und Bringfahrer",
    "Fahrzeugrückführung",
    "Fahrer Autovermietung",
    "Mitarbeiter Fahrzeuglogistik",
)


def _group(title: str, body: str, *, location: str = "17235 Neustrelitz") -> CanonicalVacancyGroup:
    record = SourceRecordPreview(
        source_id="ba",
        source_name="BA",
        external_id=title[:32],
        source_reference=None,
        title=title,
        company="Auto Logistik GmbH",
        location=location,
        posted_at="2026-09-27",
        detail_url="https://example.org/job",
        raw_payload={"description": body},
    )
    normalized = VacancyNormalizer().normalize_source_record(record)
    return SourceMergeService().merge_records((normalized,)).canonical_groups[0]


@pytest.fixture()
def vehicle_profile() -> SearchProfileContext:
    """Профиль, какой человек собрал бы через форму: категория B, немецкий A1."""
    return SearchProfileContext(
        profile_label="Перегон автомобилей",
        profile_source="saved",
        german_level="A1",
        english_level="A1",
        desired_roles=("Перегон автомобилей",),
        search_query_terms=("Fahrzeugüberführer", "Überführungsfahrer"),
        additional_search_terms=("PKW-Rangierer",),
        driver_license="B",
        home_city="Neustrelitz",
        preferred_locations=("Neustrelitz", "Berlin"),
        search_cities=("Neustrelitz", "Berlin"),
        search_radius_km=50,
        employment_types=("full_time",),
        min_salary_eur_per_hour=16.0,
        self_employment_ok=False,
        physical_work_ok=None,
        relocation_ready=False,
        shift_ok=True,
    )


def _evaluate(group: CanonicalVacancyGroup, profile: SearchProfileContext):
    signals = inspect_vacancy(group, profile)
    filter_result = FilterEngine().evaluate(
        group, profile, signals=signals, search_mode="germany_local"
    )
    score = VacancyScorer().score(group, profile, signals=signals, filter_result=filter_result)
    return signals, filter_result, score


# ---------------------------------------------------------------------------
# 2. Формирование специализированных немецких запросов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", VEHICLE_QUERIES)
def test_every_target_query_is_recognized_as_vehicle_logistics(query: str) -> None:
    intent = normalize_role_intent(query)
    assert intent is not None, query
    assert intent.family is RoleFamily.VEHICLE_LOGISTICS


@pytest.mark.parametrize("query", VEHICLE_QUERIES)
def test_specific_query_is_not_replaced_by_a_generic_one(query: str) -> None:
    """«Überführungsfahrer» не должен уходить в источник как «fahrer»."""
    intent = normalize_role_intent(query)
    assert intent is not None
    assert intent.primary_de.casefold() not in {"fahrer", "logistik", "kurier", "zusteller"}


@pytest.mark.parametrize(
    "query",
    ["перегон автомобилей", "перегон авто", "перегонщик", "автологистика", "перегін автомобілів"],
)
def test_russian_and_ukrainian_wording_translates_to_german(query: str) -> None:
    """Русское слово источник не понимает — для него нужен немецкий ключевик."""
    intent = normalize_role_intent(query)
    assert intent is not None, query
    assert intent.family is RoleFamily.VEHICLE_LOGISTICS
    # Немецкое слово, а не исходная кириллица: русский запрос источник не понимает.
    assert not re.search(r"[а-яёіїєґ]", intent.primary_de, re.IGNORECASE)


def test_fallback_keywords_stay_inside_vehicle_logistics() -> None:
    """Расширение не уходит в доставку посылок и не деградирует до «helfer»."""
    intent = normalize_role_intent("Fahrzeugüberführer")
    assert intent is not None

    keywords = get_intent_fallback_keywords(
        intent=intent, primary_query=intent.primary_de, low_language=True
    )

    assert keywords, "план расширения не должен быть пустым"
    forbidden = ("zusteller", "paketzusteller", "helfer", "kurier", "lagerhelfer", "reinigungskraft")
    for keyword in keywords:
        assert not any(token in keyword.casefold() for token in forbidden), keyword


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Fahrzeugüberführer (m/w/d)", RoleFamily.VEHICLE_LOGISTICS),
        ("Überführungsfahrer", RoleFamily.VEHICLE_LOGISTICS),
        ("Fahrer Fahrzeuglogistik", RoleFamily.VEHICLE_LOGISTICS),
        ("PKW-Rangierer", RoleFamily.VEHICLE_LOGISTICS),
        ("Werkstattfahrer", RoleFamily.VEHICLE_LOGISTICS),
        ("Fahrzeugaufbereiter", RoleFamily.CLEANING),
        # Соседние профессии остаются собой: слово "Fahrzeug" само по себе ничего
        # не решает, иначе автомеханик и производство попали бы в перегон.
        ("Kfz-Mechatroniker", RoleFamily.GENERIC),
        ("Paketzusteller", RoleFamily.DRIVING),
        ("Staplerfahrer", RoleFamily.WAREHOUSE),
        ("Lagermitarbeiter", RoleFamily.WAREHOUSE),
        ("Sachbearbeiter Autovermietung", RoleFamily.OFFICE),
    ],
)
def test_vacancy_titles_classify_correctly(title: str, expected: RoleFamily) -> None:
    from app.services.hashers import normalize_text_for_fingerprint

    assert classify_vacancy_de(normalize_text_for_fingerprint(title)) is expected


# ---------------------------------------------------------------------------
# 3. Исключение нерелевантной доставки посылок
# ---------------------------------------------------------------------------


def test_parcel_delivery_is_rejected_for_a_vehicle_logistics_profile(
    vehicle_profile: SearchProfileContext,
) -> None:
    group = _group(
        "Paketzusteller (m/w/d)",
        "Zustellung von Paketen an Privatkunden, täglich viele Zustellstopps, "
        "Tür zu Tür. Führerschein Klasse B. Vollzeit.",
    )

    _, filter_result, _ = _evaluate(group, vehicle_profile)

    assert filter_result.hard_reject is True
    assert "profession_family_mismatch" in {hit.code for hit in filter_result.rejection_hits}


def test_vehicle_transfer_is_not_rejected_for_a_courier_profile_by_accident() -> None:
    """Обратная сторона того же разделения: перегон — не работа курьера.

    Курьерскому профилю перегон не навязывается. Кому нужно и то и другое,
    перечисляет обе роли в своём профиле — набор семейств складывается из всех.
    """
    courier = SearchProfileContext(
        profile_label="Курьер",
        profile_source="saved",
        german_level="A1",
        desired_roles=("курьер",),
        driver_license="B",
    )
    both = SearchProfileContext(
        profile_label="Курьер и перегон",
        profile_source="saved",
        german_level="A1",
        desired_roles=("курьер", "перегон автомобилей"),
        driver_license="B",
    )
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Sie überführen PKW zwischen unseren Niederlassungen. Führerschein Klasse B.",
    )

    _, courier_result, _ = _evaluate(group, courier)
    _, both_result, _ = _evaluate(group, both)

    assert courier_result.hard_reject is True
    assert both_result.hard_reject is False


# ---------------------------------------------------------------------------
# 4. Исключение обязательных категорий C/CE при категории B
# ---------------------------------------------------------------------------


def test_profile_with_category_b_is_treated_as_a_driving_profile(
    vehicle_profile: SearchProfileContext,
) -> None:
    """Перегон — работа за рулём: ограничения по классу транспорта обязаны работать."""
    assert is_b_only_driving_profile(vehicle_profile) is True


def test_heavy_truck_transfer_is_rejected_for_category_b(
    vehicle_profile: SearchProfileContext,
) -> None:
    group = _group(
        "Überführungsfahrer LKW (m/w/d)",
        "Überführung von LKW und Sattelzugmaschinen. Führerschein CE erforderlich, "
        "Berufskraftfahrerqualifikation und Fahrerkarte.",
    )

    _, filter_result, _ = _evaluate(group, vehicle_profile)

    codes = {hit.code for hit in filter_result.rejection_hits}
    assert filter_result.hard_reject is True
    assert "driver_license_mismatch" in codes
    assert "heavy_vehicle_mismatch" in codes


def test_light_vehicle_transfer_passes_for_category_b(
    vehicle_profile: SearchProfileContext,
) -> None:
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Sie überführen PKW und Transporter bis 3,5 t zwischen unseren Niederlassungen. "
        "Führerschein Klasse B erforderlich. Vollzeit, unbefristet, Stundenlohn 16,50 € brutto. "
        "Quereinsteiger willkommen, Deutschkenntnisse von Vorteil.",
    )

    _, filter_result, score = _evaluate(group, vehicle_profile)

    assert filter_result.hard_reject is False
    assert score.score >= HOT_BUCKET_MIN_SCORE


# ---------------------------------------------------------------------------
# 5. Распознавание самозанятости
# ---------------------------------------------------------------------------


def test_self_employment_is_rejected_when_the_profile_requires_employment(
    vehicle_profile: SearchProfileContext,
) -> None:
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW auf selbstständiger Basis. Gewerbeschein erforderlich, "
        "Bezahlung pro Auftrag als Subunternehmer. Führerschein Klasse B.",
    )

    signals, filter_result, _ = _evaluate(group, vehicle_profile)

    assert signals.requires_self_employment is True
    assert filter_result.hard_reject is True
    assert "self_employment_mismatch" in {hit.code for hit in filter_result.rejection_hits}


def test_self_employment_is_only_a_risk_when_the_profile_says_nothing() -> None:
    """Молчание профиля не повод скрывать вакансию — только помечать риском."""
    profile = SearchProfileContext(
        profile_label="Перегон",
        profile_source="saved",
        german_level="A1",
        desired_roles=("Перегон автомобилей",),
        driver_license="B",
        self_employment_ok=None,
    )
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW auf selbstständiger Basis, Gewerbeschein erforderlich. "
        "Führerschein Klasse B.",
    )

    _, filter_result, score = _evaluate(group, profile)

    assert filter_result.hard_reject is False
    assert filter_result.review_required is True
    assert "self_employment_review" in {hit.code for hit in filter_result.review_hits}
    assert "self_employment_unknown" in {hit.code for hit in score.negative_hits}


def test_employed_contract_wording_cancels_the_self_employment_signal(
    vehicle_profile: SearchProfileContext,
) -> None:
    """«Festanstellung, keine Subunternehmer» — это не самозанятость."""
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Wir bieten eine Festanstellung mit unbefristetem Arbeitsvertrag, "
        "keine Subunternehmer. Führerschein Klasse B. Stundenlohn 17,00 € brutto.",
    )

    signals, filter_result, _ = _evaluate(group, vehicle_profile)

    assert signals.requires_self_employment is False
    assert filter_result.hard_reject is False


# ---------------------------------------------------------------------------
# 6. Немецкий язык и неизвестные языковые требования
# ---------------------------------------------------------------------------


def test_strong_german_requirement_is_rejected_for_an_a1_profile(
    vehicle_profile: SearchProfileContext,
) -> None:
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Verhandlungssicheres Deutsch in Wort und Schrift erforderlich. "
        "Führerschein Klasse B. Kundenkontakt.",
    )

    _, filter_result, _ = _evaluate(group, vehicle_profile)

    assert filter_result.hard_reject is True
    assert "strong_german_mismatch" in {hit.code for hit in filter_result.rejection_hits}


def test_optional_german_does_not_reject(vehicle_profile: SearchProfileContext) -> None:
    """«Deutschkenntnisse von Vorteil» — приглашение, а не барьер."""
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Sie überführen PKW zwischen Niederlassungen. Deutschkenntnisse von Vorteil. "
        "Führerschein Klasse B. Vollzeit, unbefristet, 16,50 € pro Stunde.",
    )

    _, filter_result, score = _evaluate(group, vehicle_profile)

    assert filter_result.hard_reject is False
    assert score.score >= MAYBE_BUCKET_MIN_SCORE


def test_unknown_language_requirement_is_not_claimed_as_absent(
    vehicle_profile: SearchProfileContext,
) -> None:
    """Без цельного описания утверждать «немецкий не требуется» нельзя."""
    group = _group("Fahrzeugüberführer (m/w/d)", "")

    signals, filter_result, _ = _evaluate(group, vehicle_profile)

    assert signals.no_mandatory_german_mentioned is False
    assert filter_result.hard_reject is False


def test_unknown_german_level_in_profile_becomes_a_risk_not_a_pass() -> None:
    """Пустой уровень немецкого раньше молча читался как «немецкий в порядке»."""
    profile = SearchProfileContext(
        profile_label="Перегон",
        profile_source="saved",
        german_level=None,
        desired_roles=("Перегон автомобилей",),
        driver_license="B",
    )
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Verhandlungssicheres Deutsch erforderlich. Führerschein Klasse B. "
        "Sie überführen Fahrzeuge zwischen Niederlassungen, unbefristet.",
    )

    _, filter_result, _ = _evaluate(group, profile)

    assert filter_result.hard_reject is False
    assert "strong_german_unknown_level" in {hit.code for hit in filter_result.review_hits}


# ---------------------------------------------------------------------------
# 7. Географические ограничения
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "city,expected_reject",
    [
        ("17235 Neustrelitz", False),
        ("17033 Neubrandenburg", False),  # 26 км — внутри радиуса 50 км
        ("10115 Berlin", False),
        ("18055 Rostock", True),  # 104 км от ближайшего заказанного города
        ("80331 München", True),
    ],
)
def test_radius_decides_geography(
    vehicle_profile: SearchProfileContext, city: str, expected_reject: bool
) -> None:
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW. Führerschein Klasse B. Vollzeit, unbefristet.",
        location=city,
    )

    _, filter_result, _ = _evaluate(group, vehicle_profile)

    codes = {hit.code for hit in filter_result.rejection_hits}
    assert ("outside_requested_cities" in codes) is expected_reject, city


def test_zero_radius_means_strictly_the_named_cities(
    vehicle_profile: SearchProfileContext,
) -> None:
    import dataclasses

    strict = dataclasses.replace(vehicle_profile, search_radius_km=0)
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW. Führerschein Klasse B.",
        location="17033 Neubrandenburg",
    )

    _, filter_result, _ = _evaluate(group, strict)

    assert "outside_requested_cities" in {hit.code for hit in filter_result.rejection_hits}


def test_unknown_location_is_not_treated_as_a_foreign_city(
    vehicle_profile: SearchProfileContext,
) -> None:
    """Молчание работодателя о месте — не повод выбрасывать вакансию."""
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW. Führerschein Klasse B. Vollzeit.",
        location="",
    )

    signals, filter_result, _ = _evaluate(group, vehicle_profile)

    assert signals.outside_requested_cities is False
    assert filter_result.hard_reject is False


# ---------------------------------------------------------------------------
# 8. Ранжирование
# ---------------------------------------------------------------------------


def test_matching_vacancy_outranks_a_poorly_paid_mini_job(
    vehicle_profile: SearchProfileContext,
) -> None:
    good = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW zwischen Niederlassungen. Führerschein Klasse B. "
        "Vollzeit, unbefristet, 17,50 € pro Stunde brutto. Quereinsteiger willkommen.",
    )
    weak = _group(
        "Fahrzeugüberführer (m/w/d) Minijob",
        "Überführung von PKW. Minijob auf 538-Euro-Basis, befristet, 12,50 € pro Stunde brutto. "
        "Führerschein Klasse B.",
    )

    _, _, good_score = _evaluate(good, vehicle_profile)
    _, weak_filter, weak_score = _evaluate(weak, vehicle_profile)

    assert good_score.score > weak_score.score
    assert "salary_above_target" in {hit.code for hit in good_score.positive_hits}
    assert "salary_below_target" in {hit.code for hit in weak_score.negative_hits}
    assert "employment_type_review" in {hit.code for hit in weak_filter.review_hits}


def test_salary_is_normalised_to_an_hourly_rate(vehicle_profile: SearchProfileContext) -> None:
    """Месячная сумма сравнивается с ориентиром после пересчёта в €/час."""
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW. Führerschein Klasse B. Vollzeit, unbefristet. "
        "Wir zahlen 3.200 € monatlich brutto.",
    )

    signals, _, score = _evaluate(group, vehicle_profile)

    assert signals.salary_period == "month"
    assert signals.salary_hourly_eur == pytest.approx(3200 / 173.0, rel=0.01)
    assert "salary_above_target" in {hit.code for hit in score.positive_hits}


def test_net_salary_is_not_compared_with_a_gross_target(
    vehicle_profile: SearchProfileContext,
) -> None:
    """Нетто в брутто по объявлению не пересчитать — сравнение не делается."""
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW. Führerschein Klasse B. 1.900 € netto auf die Hand.",
    )

    signals, _, score = _evaluate(group, vehicle_profile)

    assert signals.salary_is_net is True
    assert signals.salary_is_comparable is False
    codes = {hit.code for hit in (*score.positive_hits, *score.negative_hits)}
    assert "salary_below_target" not in codes
    assert "salary_above_target" not in codes


def test_unknown_salary_is_not_penalised(vehicle_profile: SearchProfileContext) -> None:
    """В Германии оплату чаще не указывают — штрафовать за это значит терять вакансии."""
    group = _group(
        "Fahrzeugüberführer (m/w/d)",
        "Überführung von PKW zwischen Niederlassungen. Führerschein Klasse B. "
        "Vollzeit, unbefristet. Bezahlung nach Vereinbarung.",
    )

    _, _, score = _evaluate(group, vehicle_profile)

    codes = {hit.code for hit in score.negative_hits}
    assert "salary_below_target" not in codes


def test_profile_role_text_recognises_the_russian_wording() -> None:
    assert classify_role_text("Перегон автомобилей") is RoleFamily.VEHICLE_LOGISTICS
