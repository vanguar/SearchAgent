from app.services.location_normalizer import LocationNormalizer


def test_location_normalizer_extracts_city_country_and_postal_code() -> None:
    normalizer = LocationNormalizer()

    location = normalizer.normalize("10115 Berlin, Deutschland")

    assert location.city == "Berlin"
    assert location.country_code == "DE"
    assert location.postal_code == "10115"
    assert location.normalized_text == "berlin, DE"


def test_location_normalizer_handles_raum_prefix_and_country_alias() -> None:
    normalizer = LocationNormalizer()

    location = normalizer.normalize("Raum München / Germany")

    assert location.city == "München"
    assert location.country_code == "DE"
    assert location.normalized_text == "munchen, DE"
