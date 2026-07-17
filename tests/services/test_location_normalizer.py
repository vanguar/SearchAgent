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


def test_location_normalizer_does_not_treat_english_at_as_austria() -> None:
    normalizer = LocationNormalizer()

    location = normalizer.normalize("Remote (work at home)")

    assert location.country_code is None  # "at" is a preposition, not the Austria ISO code


def test_location_normalizer_detects_uppercase_iso_country_code() -> None:
    normalizer = LocationNormalizer()

    location = normalizer.normalize("Vienna, AT")

    assert location.country_code == "AT"
    assert location.city == "Vienna"
