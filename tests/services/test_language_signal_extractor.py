from app.services.language_signal_extractor import LanguageSignalExtractor


def test_language_signal_extractor_detects_strong_german_shift_and_helper_signals() -> None:
    extractor = LanguageSignalExtractor()

    signals = extractor.extract(
        title="Lagermitarbeiter (m/w/d)",
        body_text="Gute Deutschkenntnisse erforderlich. 3 Schicht. Englisch ist ein Plus.",
    )

    assert signals.strong_german_required is True
    assert signals.german_mentioned is True
    assert signals.english_mentioned is True
    assert signals.shift_signal is True
    assert signals.helper_role_signal is True


def test_language_signal_extractor_detects_low_language_signal() -> None:
    extractor = LanguageSignalExtractor()

    signals = extractor.extract(
        title="Warehouse Helper",
        body_text="Ohne Deutsch. English only team.",
    )

    assert signals.low_language_signal is True
    assert signals.helper_role_signal is True
