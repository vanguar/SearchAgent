from app.services.driver_license_signal_extractor import extract_driver_license_requirements


def test_extracts_required_higher_driver_license_categories() -> None:
    cases = (
        ("Führerschein Klasse C", ("C",)),
        ("Führerscheinklasse C", ("C",)),
        ("Führerschein der Klasse C", ("C",)),
        ("Fahrerlaubnis C1", ("C1",)),
        ("Fahrerlaubnis der Klasse C1", ("C1",)),
        ("Führerschein Klasse CE", ("CE",)),
        ("Fahrerlaubnis Kat. CE", ("CE",)),
        ("FS-Klasse CE", ("CE",)),
        ("Klasse C/CE", ("C", "CE")),
        ("C + CE", ("C", "CE")),
        ("C/CE-Führerschein", ("C", "CE")),
        ("Fahrerlaubnisklasse C1E erforderlich", ("C1E",)),
        ("Führerschein Klasse D", ("D",)),
        ("Führerschein DE", ("DE",)),
        ("LKW-Führerschein C", ("C",)),
        ("LKW Führerschein Klasse C/CE", ("C", "CE")),
        ("LKW Fahrer Kl. CE (m/w/d) im Fernverkehr", ("CE",)),
        ("Valid driving licence class C required", ("C",)),
        ("Driver's license category CE mandatory", ("CE",)),
        ("HGV licence class C/CE required", ("C", "CE")),
        ("C1E driving licence required", ("C1E",)),
        ("CE licence required", ("CE",)),
    )

    for text, expected in cases:
        result = extract_driver_license_requirements(text)

        assert result.required == expected, text


def test_extracts_b_alternatives_and_optional_higher_categories() -> None:
    alternatives = (
        "Führerschein Klasse B oder C",
        "Fahrerlaubnis B/C",
        "Klasse B oder CE",
        "Driving licence class B or C",
        "Driver's license B/C",
    )
    optional = (
        "CE von Vorteil",
        "Klasse C wünschenswert",
        "C-Führerschein wäre ideal",
        "Führerschein CE nicht erforderlich",
        "Class CE preferred",
        "Driving licence class C not required",
        "CE licence desirable",
    )

    for text in alternatives:
        result = extract_driver_license_requirements(text)

        assert "B" in result.allowed, text
        assert any(category != "B" for category in result.mentioned), text
        assert not result.required, text

    for text in optional:
        result = extract_driver_license_requirements(text)

        assert result.optional, text
        assert result.mentioned, text
        assert not result.required, text


def test_collects_every_explicit_category_mention_for_strict_b_only_matching() -> None:
    cases = (
        ("Führerschein Klasse B oder C", ("B", "C")),
        ("Fahrerlaubnis B/C", ("B", "C")),
        ("B erforderlich, C von Vorteil", ("B", "C")),
        ("CE wünschenswert", ("CE",)),
        ("CE optional", ("CE",)),
        ("CE nicht erforderlich", ("CE",)),
        ("Klasse B ausreichend, CE nicht notwendig", ("B", "CE")),
        ("Führerschein B, C kann später erworben werden", ("B", "C")),
        ("Führerschein Klasse C/CE", ("C", "CE")),
        ("LKW Fahrer Kl. CE", ("CE",)),
        ("Fahrer Klasse B oder C1", ("B", "C1")),
        ("Führerschein B, idealerweise C1E", ("B", "C1E")),
        ("Führerschein B, Klasse D von Vorteil", ("B", "D")),
        ("Führerschein Klasse D1/D1E", ("D1", "D1E")),
        ("Führerschein Klasse B96", ("B96",)),
        ("Führerschein Klasse B196", ("B196",)),
        ("Führerschein Klasse BE", ("BE",)),
        ("LKW-Führerschein", ("LKW",)),
        ("HGV licence", ("LKW",)),
        ("LGV license", ("LKW",)),
    )

    for text, expected in cases:
        result = extract_driver_license_requirements(text)

        assert result.mentioned == expected, text


def test_does_not_mistake_gender_marker_or_language_level_for_license_category() -> None:
    gender_marker = extract_driver_license_requirements("Fahrer Klasse B (m/w/d)")
    language_level = extract_driver_license_requirements("Führerschein Klasse B erforderlich. Deutsch C1 erforderlich.")

    assert gender_marker.required == ("B",)
    assert language_level.required == ("B",)
    assert language_level.mentioned == ("B",)


def test_does_not_treat_non_license_category_language_as_driver_license_mentions() -> None:
    cases = (
        "Vitamin C",
        "Vitamin C erforderlich",
        "Category C product",
        "CE certification",
        "Class C software",
        "C-Level management",
    )

    for text in cases:
        result = extract_driver_license_requirements(text)

        assert not result.mentioned, text


def test_mixed_required_and_optional_higher_categories_keep_required_signal() -> None:
    result = extract_driver_license_requirements("CE erforderlich und Klasse C wünschenswert")

    assert result.required == ("CE",)
    assert result.optional == ("C",)
