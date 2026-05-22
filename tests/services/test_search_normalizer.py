from app.services.search_normalizer import normalize_search_location_for_profile


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
