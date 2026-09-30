"""Заданный город должен находиться целиком.

«Задал Берлин — найди вакансии по Берлину» звучит просто, но источники пишут
место десятком способов: «10247 Berlin», «Berlin-Kreuzberg», «Friedrichshain,
Berlin», «Berlin (Zentrale)», а иногда не пишут вовсе. Каждый из этих способов
обязан попасть в раздел Берлина, а чужой город — не попасть.

Здесь же проверяется, что «по локации не понять» не превращается в отказ: молчание
работодателя о месте не повод выбрасывать вакансию.
"""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine
from app.services.geo_distance import location_matches_city
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.rule_catalog import inspect_vacancy
from app.services.search_models import SearchProfileContext
from app.services.search_normalizer import parse_search_cities
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService

# Как немецкие источники на самом деле записывают берлинский адрес.
BERLIN_SPELLINGS: tuple[str, ...] = (
    "Berlin",
    "10115 Berlin",
    "10247 Berlin",
    "Berlin, Berlin",
    "Berlin (Zentrale)",
    "Berlin-Mitte",
    "Berlin Mitte",
    "Berlin-Kreuzberg",
    "Berlin Kreuzberg",
    "Berlin-Charlottenburg",
    "Berlin Friedrichshain",
    "Berlin-Spandau",
    "Berlin-Biesdorf",
    "Friedrichshain, Berlin",
    "Kreuzberg, Berlin",
    "12459 Berlin-Schöneweide",
    "Berlin und Umgebung",
    "Raum Berlin",
    "Berlin, Deutschland",
    "Berlin, DE",
)

# Города, которые источники досыпают к Берлину, но заказаны они не были.
NEARBY_OTHER_CITIES: tuple[str, ...] = (
    "Potsdam",
    "Ludwigsfelde",
    "Oranienburg",
    "Königs Wusterhausen",
)


def _group(location: str, *, title: str = "Lagermitarbeiter (m/w/d)") -> CanonicalVacancyGroup:
    record = SourceRecordPreview(
        source_id="ba",
        source_name="BA",
        external_id=f"{title}-{location}"[:48],
        source_reference=None,
        title=title,
        company="Beispiel GmbH",
        location=location,
        posted_at="2026-09-27",
        detail_url="https://example.org/job",
        raw_payload={
            "description": (
                "Kommissionierung und Verladung im Lager. Schichtarbeit, unbefristet, "
                "15,00 € pro Stunde brutto. Quereinsteiger willkommen."
            )
        },
    )
    return SourceMergeService().merge_records(
        (VacancyNormalizer().normalize_source_record(record),)
    ).canonical_groups[0]


def _berlin_profile(**kwargs) -> SearchProfileContext:
    base = {
        "profile_label": "Склад в Берлине",
        "profile_source": "saved",
        "german_level": "A2",
        "desired_roles": ("склад",),
        "preferred_locations": ("Berlin",),
        "search_cities": ("Berlin",),
        "relocation_ready": False,
        "shift_ok": True,
    }
    base.update(kwargs)
    return SearchProfileContext(**base)


def _evaluate(group: CanonicalVacancyGroup, profile: SearchProfileContext):
    signals = inspect_vacancy(group, profile)
    filter_result = FilterEngine().evaluate(
        group, profile, signals=signals, search_mode="germany_local"
    )
    return signals, filter_result


# ---------------------------------------------------------------------------
# Что уходит в источники
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("Берлин", ("Berlin",)),
        ("Berlin", ("Berlin",)),
        ("берлин", ("Berlin",)),
        ("Берлін", ("Berlin",)),
        ("Berlin, Deutschland", ("Berlin",)),
        ("Берлин, Росток", ("Berlin", "Rostock")),
        ("Берлин, Berlin", ("Berlin",)),
        ("Нойштрелиц", ("Neustrelitz",)),
    ],
)
def test_typed_city_becomes_a_german_query(typed: str, expected: tuple[str, ...]) -> None:
    """Источник получает немецкое написание, а порядок — как человек набрал."""
    assert parse_search_cities(typed) == expected


# ---------------------------------------------------------------------------
# Все способы записать Берлин попадают в Берлин
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", BERLIN_SPELLINGS)
def test_every_berlin_spelling_matches_berlin(spelling: str) -> None:
    assert location_matches_city(spelling, "Berlin") is True, spelling


@pytest.mark.parametrize("spelling", BERLIN_SPELLINGS)
def test_every_berlin_spelling_survives_the_city_filter(spelling: str) -> None:
    signals, filter_result = _evaluate(_group(spelling), _berlin_profile())

    assert signals.outside_requested_cities is False, spelling
    assert "outside_requested_cities" not in {hit.code for hit in filter_result.rejection_hits}
    assert "location_mismatch" not in {hit.code for hit in filter_result.rejection_hits}


@pytest.mark.parametrize("location", ["Kyiv, Ukraine", "Warszawa, Polska", "Remote, Worldwide"])
def test_worldwide_remote_run_keeps_foreign_locations(location: str) -> None:
    """В удалённом поиске зарубежная локация — норма, а не причина отказа."""
    profile = _berlin_profile(preferred_locations=("worldwide remote",), search_cities=())
    group = _group(location)
    signals = inspect_vacancy(group, profile)

    filter_result = FilterEngine().evaluate(
        group, profile, signals=signals, search_mode="remote_worldwide"
    )

    assert "outside_requested_cities" not in {hit.code for hit in filter_result.rejection_hits}
    assert "location_mismatch" not in {hit.code for hit in filter_result.rejection_hits}


@pytest.mark.parametrize("spelling", BERLIN_SPELLINGS)
def test_every_berlin_spelling_lands_in_the_berlin_section(spelling: str) -> None:
    """Раздел выдачи по городу берётся из matched_search_city."""
    signals, _ = _evaluate(_group(spelling), _berlin_profile())

    assert signals.matched_search_city == "Berlin", spelling


# ---------------------------------------------------------------------------
# Чужие города не попадают
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("city", NEARBY_OTHER_CITIES)
def test_neighbouring_city_is_rejected_without_a_radius(city: str) -> None:
    """Без радиуса заказ — это сами города, а не их окрестности."""
    _, filter_result = _evaluate(_group(city), _berlin_profile())

    assert "outside_requested_cities" in {hit.code for hit in filter_result.rejection_hits}, city


@pytest.mark.parametrize("city", ["Potsdam", "Oranienburg"])
def test_neighbouring_city_is_accepted_within_a_radius(city: str) -> None:
    """С радиусом 50 км пригороды Берлина входят в заказ."""
    _, filter_result = _evaluate(_group(city), _berlin_profile(search_radius_km=50))

    assert "outside_requested_cities" not in {hit.code for hit in filter_result.rejection_hits}, city


def test_far_city_stays_rejected_even_with_a_radius() -> None:
    _, filter_result = _evaluate(_group("München"), _berlin_profile(search_radius_km=50))

    assert "outside_requested_cities" in {hit.code for hit in filter_result.rejection_hits}


# ---------------------------------------------------------------------------
# Несколько городов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location,expected_city",
    [
        ("10115 Berlin", "Berlin"),
        ("Berlin-Kreuzberg", "Berlin"),
        ("18055 Rostock", "Rostock"),
        ("Rostock-Evershagen", "Rostock"),
    ],
)
def test_multi_city_order_assigns_each_vacancy_to_its_own_city(
    location: str, expected_city: str
) -> None:
    profile = _berlin_profile(
        preferred_locations=("Berlin", "Rostock"), search_cities=("Berlin", "Rostock")
    )

    signals, filter_result = _evaluate(_group(location), profile)

    assert filter_result.hard_reject is False, location
    assert signals.matched_search_city == expected_city, location


def test_city_outside_a_multi_city_order_is_rejected() -> None:
    profile = _berlin_profile(
        preferred_locations=("Berlin", "Rostock"), search_cities=("Berlin", "Rostock")
    )

    _, filter_result = _evaluate(_group("Hamburg"), profile)

    assert "outside_requested_cities" in {hit.code for hit in filter_result.rejection_hits}


# ---------------------------------------------------------------------------
# «Не понять» — это не отказ
# ---------------------------------------------------------------------------


def test_vacancy_without_a_location_is_kept() -> None:
    """Источник не сообщил место — это не повод выбрасывать вакансию.

    Вакансию вернули по запросу нужного города; молчание работодателя об адресе
    к делу не относится.
    """
    signals, filter_result = _evaluate(_group(""), _berlin_profile())

    assert signals.outside_requested_cities is False
    assert filter_result.hard_reject is False


def test_unrecognised_place_name_is_kept() -> None:
    """Названия, которого нет в справочнике, недостаточно, чтобы отклонить."""
    signals, filter_result = _evaluate(_group("Gewerbegebiet Nordwest"), _berlin_profile())

    assert signals.outside_requested_cities is False
    assert filter_result.hard_reject is False


def test_bare_district_name_shared_with_another_town_is_kept() -> None:
    """«Biesdorf» — и берлинский район, и город в Айфеле; по слову их не различить."""
    signals, filter_result = _evaluate(_group("Biesdorf"), _berlin_profile())

    assert signals.outside_requested_cities is False
    assert filter_result.hard_reject is False


def test_ambiguous_district_is_still_filtered_by_radius() -> None:
    """С радиусом неоднозначное имя проверяется по расстоянию, а не по слову."""
    _, filter_result = _evaluate(_group("Biesdorf"), _berlin_profile(search_radius_km=50))

    assert "outside_requested_cities" in {hit.code for hit in filter_result.rejection_hits}


# ---------------------------------------------------------------------------
# Другая страна — это однозначно не заказанный город
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    ["Kyiv, Ukraine", "Wien, AT", "Wien, Österreich", "Warszawa, Polska", "Zürich, Schweiz"],
)
def test_foreign_location_is_rejected_for_a_german_city_order(location: str) -> None:
    """Немецкого справочника для такой локации нет, но страна названа прямо."""
    _, filter_result = _evaluate(_group(location), _berlin_profile())

    assert "outside_requested_cities" in {hit.code for hit in filter_result.rejection_hits}, location


@pytest.mark.parametrize("location", ["Berlin, Deutschland", "Frankfurt, Deutschland", "10247 Berlin"])
def test_explicit_germany_is_not_mistaken_for_a_foreign_country(location: str) -> None:
    profile = _berlin_profile(
        preferred_locations=("Berlin", "Frankfurt"), search_cities=("Berlin", "Frankfurt")
    )

    _, filter_result = _evaluate(_group(location), profile)

    assert "outside_requested_cities" not in {hit.code for hit in filter_result.rejection_hits}, location


def test_country_name_in_the_body_does_not_move_the_vacancy_abroad() -> None:
    """Проверяется только поле локации: в описании страны упоминают сплошь."""
    record_group = _group("10247 Berlin")

    _, filter_result = _evaluate(record_group, _berlin_profile())

    assert filter_result.hard_reject is False


# ---------------------------------------------------------------------------
# Удалённый поиск географию не ограничивает
# ---------------------------------------------------------------------------


def test_worldwide_remote_run_ignores_city_filters() -> None:
    profile = _berlin_profile(preferred_locations=("worldwide remote",), search_cities=())
    signals = inspect_vacancy(_group("München"), profile)

    filter_result = FilterEngine().evaluate(
        _group("München"), profile, signals=signals, search_mode="remote_worldwide"
    )

    assert "outside_requested_cities" not in {hit.code for hit in filter_result.rejection_hits}
    assert "location_mismatch" not in {hit.code for hit in filter_result.rejection_hits}
