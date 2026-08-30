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


def test_language_signal_extractor_treats_no_or_a1_german_as_low_barrier() -> None:
    extractor = LanguageSignalExtractor()

    for body in (
        "Deutschkenntnisse nicht erforderlich.",
        "Kein Deutsch erforderlich.",
        "German is not required.",
        "Deutschkenntnisse A1 ausreichend.",
        "A1 Deutsch genügt.",
    ):
        signals = extractor.extract(title="Hilfskraft", body_text=body)

        assert signals.low_language_signal is True, body


def test_english_source_text_does_not_imply_english_requirement() -> None:
    signals = LanguageSignalExtractor().extract(
        title="AI Automation Specialist",
        body_text="Build workflows with Claude and GPT for internal teams.",
    )

    assert signals.english_required is False
    assert signals.english_preferred is False


def test_explicit_english_required_and_preferred_are_distinct() -> None:
    extractor = LanguageSignalExtractor()

    required = extractor.extract(
        title="AI Specialist",
        body_text="Business English mandatory. English C1.",
    )
    preferred = extractor.extract(
        title="AI Specialist",
        body_text="English is a plus and preferred.",
    )

    assert required.english_required is True
    assert preferred.english_required is False
    assert preferred.english_preferred is True
