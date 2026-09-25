import pytest
from app.services.search_normalizer import (
    is_country_wide_location,
    normalize_location,
    normalize_search_location_for_profile,
    parse_search_cities,
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


# ---------------------------------------------------------------------------
# Список городов из поля поиска
# ---------------------------------------------------------------------------

def test_two_cities_are_read_as_two_cities() -> None:
    """Поле «Берлин, Росток» раньше уходило в источники одной строкой.

    BA молча сводил её к первому городу, Careerjet терял фильтр целиком, и
    поиск по второму городу просто не происходил.
    """
    assert parse_search_cities("Берлин, Росток") == ("Berlin", "Rostock")


def test_city_order_from_the_form_is_preserved() -> None:
    """Порядок городов — это приоритет, он же порядок разделов в выдаче."""
    assert parse_search_cities("Росток, Берлин") == ("Rostock", "Berlin")


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Берлин", "Berlin"),          # русский
        ("Берлін", "Berlin"),          # украинский
        ("Munich", "München"),         # английский
        ("München", "München"),        # немецкий
        ("munchen", "München"),        # немецкий без умляута
    ],
)
def test_city_is_understood_in_every_language_the_user_types(typed: str, expected: str) -> None:
    assert parse_search_cities(typed) == (expected,)


def test_city_outside_the_dictionary_is_resolved_by_transliteration() -> None:
    """Перечислить всю Германию словарём нельзя, а искать в Гюстрове надо."""
    assert normalize_location("Гюстров") == "Güstrow"
    assert normalize_location("Нойбранденбург") == "Neubrandenburg"


def test_same_city_written_twice_is_asked_once() -> None:
    assert parse_search_cities("Берлин, Berlin, berlin") == ("Berlin",)


def test_country_next_to_a_city_does_not_become_a_city() -> None:
    """«Berlin, Deutschland» — это уточнение страны, а не заказ всей страны."""
    assert parse_search_cities("Berlin, Deutschland") == ("Berlin",)


@pytest.mark.parametrize("location", ["Deutschland", "remote", "", None])
def test_no_cities_means_no_city_filter(location: str | None) -> None:
    """Пустой список — это «города не заданы», а не «города не найдены»."""
    assert parse_search_cities(location) == ()


def test_profile_with_several_cities_prefills_all_of_them() -> None:
    """Профиль «Росток, Штральзунд, Грайфсвальд» подставлял в форму один Росток."""
    result = normalize_search_location_for_profile(
        ["Rostock", "Stralsund", "Greifswald"],
        relocation_ready=True,
    )

    assert result == "Rostock, Stralsund, Greifswald"
