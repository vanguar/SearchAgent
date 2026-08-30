from app.services.profile_location_sanitizer import resolve_remote_intent, sanitize_preferred_locations


def test_sanitizer_removes_search_intent_and_keeps_geography() -> None:
    assert sanitize_preferred_locations(
        [
            "international remote companies",
            "worldwide remote",
            "Rostock",
            "Stralsund",
            "Greifswald",
        ]
    ) == ["Rostock", "Stralsund", "Greifswald"]
    assert sanitize_preferred_locations(["remote", "Berlin"]) == ["Berlin"]
    assert sanitize_preferred_locations(["Deutschland", "worldwide remote"]) == ["Deutschland"]


def test_sanitizer_deduplicates_case_insensitively_and_keeps_unknown_geography() -> None:
    assert sanitize_preferred_locations(["Rostock", "rostock", " Rostock "]) == ["Rostock"]
    assert sanitize_preferred_locations(["Mecklenburg-Vorpommern"]) == ["Mecklenburg-Vorpommern"]
    assert sanitize_preferred_locations(["Neustrelitz"]) == ["Neustrelitz"]
    assert sanitize_preferred_locations(["Österreich"]) == ["Österreich"]


def test_sanitizer_removes_supported_english_and_russian_remote_phrases() -> None:
    intent_phrases = (
        "remote worldwide",
        "international remote",
        "remote companies",
        "worldwide companies",
        "international companies",
        "global remote",
        "fully remote",
        "100% remote",
        "homeoffice",
        "home office",
        "work from home",
        "удалённо",
        "удаленно",
        "удалённая работа",
        "удаленная работа",
        "дистанционно",
        "дистанционная работа",
        "по всему миру",
        "удалённо по всему миру",
        "удаленно по всему миру",
        "международные удалённые компании",
        "международные удаленные компании",
        "международные компании",
    )

    assert sanitize_preferred_locations([*intent_phrases, "Hamburg"]) == ["Hamburg"]


def test_remote_negation_overrides_extracted_remote_signals() -> None:
    for text in (
        "Удалённая работа не нужна",
        "Не ищу remote",
        "Только локальная работа, не remote",
    ):
        assert resolve_remote_intent(
            text,
            extracted_remote_allowed=True,
            extracted_international_remote_allowed=True,
            extracted_work_modes=["remote", "office"],
        ) == (False, False)


def test_positive_remote_and_worldwide_intent_are_preserved_outside_locations() -> None:
    assert resolve_remote_intent(
        "Ищу удалённую работу в международных компаниях по всему миру",
        extracted_remote_allowed=None,
        extracted_international_remote_allowed=None,
    ) == (True, True)
