import pytest
from app.services.search_normalizer import (
    is_country_wide_location,
    normalize_search_location_for_profile,
)


def test_remote_worldwide_profile_prefills_remote_location() -> None:
    result = normalize_search_location_for_profile(
        ["worldwide remote", "Germany", "EU", "UK", "USA", "Canada"],
        relocation_ready=False,
    )

    assert result == "remote"


def test_regular_germany_profile_still_prefills_deutschland() -> None:
    result = normalize_search_location_for_profile(["Deutschland"], relocation_ready=True)

    assert result == "Deutschland"


def test_regular_city_profile_still_prefills_city_when_not_remote_worldwide() -> None:
    result = normalize_search_location_for_profile(["Rostock"], relocation_ready=False)

    assert result == "Rostock"


@pytest.mark.parametrize(
    "location",
    ["Deutschland", "deutschland", "Germany", "по всей Германии", "Deutschland, Germany"],
)
def test_country_name_means_the_radius_does_not_apply(location: str) -> None:
    """Радиус отмеряется от города; от страны мерить не от чего.

    Раньше значение молча игнорировалось: форма показывала «25 км», а поиск
    возвращал вакансии со всей Германии.
    """
    assert is_country_wide_location(location)


@pytest.mark.parametrize("location", ["Rostock", "Berlin", "Rostock, Deutschland", "", None])
def test_a_named_place_keeps_the_radius(location: str | None) -> None:
    assert not is_country_wide_location(location)
