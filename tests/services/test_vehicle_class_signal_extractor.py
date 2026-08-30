from app.services.vehicle_class_signal_extractor import extract_vehicle_class_signals


def test_extracts_heavy_vehicle_signals() -> None:
    cases = (
        ("LKW Fahrer im Fernverkehr", "LKW"),
        ("Berufskraftfahrer im Fernverkehr", "Berufskraftfahrer"),
        ("Fahrmischerfahrer", "Fahrmischer"),
        ("Fahrer Betonmischer", "Betonmischer"),
        ("Kipperfahrer", "Kipper"),
        ("Fahrer für Kippsattel", "Kipper"),
        ("Kraftfahrer Silofahrzeug", "Silofahrzeug"),
        ("Tankwagenfahrer", "Tankwagen"),
        ("Sattelzugfahrer", "Sattelzug"),
        ("Fahrer 40-Tonner", "40 t"),
        ("Fahrer 7,5 t", "7,5 t"),
        ("Busfahrer Klasse D", "Bus"),
    )

    for text, expected in cases:
        result = extract_vehicle_class_signals(text)

        assert expected in result.heavy_vehicle, text


def test_extracts_professional_heavy_driver_qualifications() -> None:
    cases = (
        ("Fahrer mit Fahrerkarte", "Fahrerkarte"),
        ("Berufskraftfahrerqualifikation Code 95 erforderlich", "Berufskraftfahrerqualifikation"),
        ("Schlüsselzahl 95 erforderlich", "Code 95"),
        ("BKrFQG Grundqualifikation", "Berufskraftfahrerqualifikation"),
        ("digitale Fahrerkarte", "Fahrerkarte"),
        ("Tachographenkarte", "Fahrerkarte"),
        ("ADR Fahrer Fernverkehr", "ADR-Schein"),
        ("ADR-Bescheinigung", "ADR-Schein"),
        ("Gefahrgutführerschein", "ADR-Schein"),
    )

    for text, expected in cases:
        result = extract_vehicle_class_signals(text)

        assert expected in result.heavy_qualification, text


def test_extracts_light_commercial_vehicle_signals() -> None:
    cases = (
        ("Sprinterfahrer Fernverkehr Klasse B", "Sprinter"),
        ("Transporterfahrer deutschlandweit", "Transporter"),
        ("Fahrer bis 3,5 t", "bis 3,5 t"),
        ("Kleintransporter Direktfahrten", "Kleintransporter"),
        ("Planensprinter Fahrer", "Planensprinter"),
        ("Koffersprinter Fahrer", "Koffersprinter"),
        ("Servicefahrer mit Kastenwagen", "Kastenwagen"),
        ("Fahrer mit PKW", "PKW"),
    )

    for text, expected in cases:
        result = extract_vehicle_class_signals(text)

        assert expected in result.light_commercial, text


def test_generic_driver_words_are_not_heavy_signals() -> None:
    for text in (
        "Fahrer",
        "Auslieferungsfahrer",
        "Kurierfahrer",
        "Servicefahrer",
        "Kraftfahrer mit Sprinter bis 3,5 t",
    ):
        result = extract_vehicle_class_signals(text)

        assert not result.heavy_vehicle, text
        assert not result.heavy_qualification, text


def test_vehicle_mass_requires_driver_or_vehicle_context() -> None:
    result = extract_vehicle_class_signals("Wir bewegen täglich bis zu 12 t Waren im Lager.")

    assert "12 t" not in result.heavy_vehicle


def test_ignores_negated_heavy_vehicle_and_qualification_mentions() -> None:
    cases = (
        "Auslieferungsfahrer - kein LKW, nur Sprinter bis 3,5 t",
        "Kurierfahrer mit Transporter - auch ohne LKW-Führerschein möglich",
        "Kleintransporter Fahrer - Sie fahren keinen LKW, sondern unseren Kleintransporter",
        "Sprinterfahrer, nicht als Berufskraftfahrer eingesetzt",
        "Fahrer Klasse B, keine Fahrerkarte erforderlich",
    )

    for text in cases:
        result = extract_vehicle_class_signals(text)

        assert not result.heavy_vehicle, text
        assert not result.heavy_qualification, text


def test_light_signal_does_not_override_non_negated_heavy_conflict() -> None:
    result = extract_vehicle_class_signals("Einsatz mit Sprinter oder Sattelzug")

    assert "Sprinter" in result.light_commercial
    assert "Sattelzug" in result.heavy_vehicle
