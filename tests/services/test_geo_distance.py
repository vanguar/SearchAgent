"""Тесты офлайн-геодистанции: координаты, районы, неизвестные места."""
from __future__ import annotations

from app.services.geo_distance import canonical_city, distance_km, resolve_point


def _km(origin: str, destination: str) -> float | None:
    return distance_km(resolve_point(city=origin), resolve_point(city=destination))


def test_resolves_towns_from_the_bundled_postal_dataset() -> None:
    # Маленький город, которого не было бы в таблице, набранной вручную.
    point = resolve_point(city="Tribsees")

    assert point is not None
    assert 54.0 < point.latitude < 54.2
    assert 12.6 < point.longitude < 12.9


def test_distances_are_symmetric_and_plausible() -> None:
    assert _km("Tribsees", "Tribsees") == 0
    assert 35 < _km("Tribsees", "Rostock") < 50
    assert 25 < _km("Tribsees", "Stralsund") < 40
    assert 600 < _km("Tribsees", "München") < 750
    assert _km("Rostock", "Berlin") == _km("Berlin", "Rostock")


def test_city_districts_resolve_to_their_parent_city() -> None:
    """Источники подставляют название района вместо города."""
    for district in ("Evershagen", "Biestow", "Brinckmansdorf", "Kassebohm", "Seebad Warnemünde"):
        assert canonical_city(district) == "rostock", district
        assert _km("Rostock", district) == 0, district


def test_curated_district_wins_over_a_same_named_municipality() -> None:
    """Diedrichshagen — и район Ростока, и община под Грайфсвальдом в 89 км.

    Почтовый датасет знает вторую; источники, подставляющие название района,
    имеют в виду первую, из-за чего вакансия DRK Rostock уезжала за радиус.
    """
    assert canonical_city("Diedrichshagen") == "rostock"
    assert _km("Rostock", "Diedrichshagen") == 0


def test_english_city_names_resolve() -> None:
    assert canonical_city("Munich") == "munchen"
    assert _km("München", "Munich") == 0


def test_postal_code_resolves_when_the_city_name_is_unknown() -> None:
    point = resolve_point(city="Irgendwo", location_text="18209 Irgendwo, Mecklenburg")

    assert point is not None
    assert 53.9 < point.latitude < 54.3


def test_unknown_place_yields_no_distance_rather_than_zero() -> None:
    assert resolve_point(city="Zzz Unbekannt") is None
    assert distance_km(resolve_point(city="Rostock"), None) is None
    assert distance_km(None, resolve_point(city="Rostock")) is None


def test_qualified_names_fall_back_to_the_head_town() -> None:
    assert _km("Rostock", "Roggentin bei Rostock") is not None
    assert _km("Freiburg", "Freiburg im Breisgau") == 0
