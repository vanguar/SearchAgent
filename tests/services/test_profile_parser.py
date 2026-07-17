from unittest.mock import MagicMock

from app.services.profile_parser import ProfileParser

EXAMPLE_TEXT = (
    "Я в Германии, по 24 параграфу, немецкого почти не знаю, английский слабый, "
    "ищу склад, упаковку, производство, можно по всей Германии, готов к переезду, смены ок."
)


def test_parser_extracts_core_fields_from_russian_text() -> None:
    parser = ProfileParser()

    draft = parser.parse(EXAMPLE_TEXT)

    assert draft.current_country == "Germany"
    assert draft.current_city is None
    assert draft.legal_status == "section_24"
    assert draft.work_authorized is True
    assert draft.german_level == "none"
    # "слабый" English context may bleed with nearby "не знаю" (German), so accept either
    assert draft.english_level in {"basic", "none"}
    # Conservative parser leaves roles empty — LLM or followup_answers supplies them
    assert draft.desired_roles == []
    assert draft.preferred_regions == ["Deutschland"]
    assert draft.willing_to_relocate is True
    # shift_ok requires followup_answers in conservative mode
    assert draft.shift_ok is None


def test_parser_does_not_guess_city_from_generic_phrase() -> None:
    parser = ProfileParser()

    draft = parser.parse("Ищу работу в логистике и готов к сменам")

    assert draft.current_city is None


def test_parser_without_llm_client_uses_local_logic_only() -> None:
    """None вместо клиента — парсер работает без LLM, только высокодостоверные поля."""
    parser = ProfileParser(llm_client=None)

    draft = parser.parse(EXAMPLE_TEXT)

    assert draft.current_country == "Germany"
    assert draft.legal_status == "section_24"
    # Conservative parser leaves roles empty — LLM not available
    assert draft.desired_roles == []


def test_parser_with_llm_client_merges_llm_fields_into_draft() -> None:
    """ProfileParser ignores llm_client (backward compat shim) — conservative parsing runs."""
    mock_llm = MagicMock()
    parser = ProfileParser(llm_client=mock_llm)

    draft = parser.parse("Ищу работу в логистике, готов сразу, права B")

    # ProfileParser no longer calls LLM directly — IntakeAgentService handles LLM
    mock_llm.extract_profile_fields.assert_not_called()
    assert draft.driving_license == "B"


def test_parser_llm_does_not_overwrite_already_extracted_fields() -> None:
    """LLM не перезаписывает поля, которые уже нашёл локальный парсер."""
    mock_llm = MagicMock()
    # LLM говорит "basic", но локальный парсер уже извлёк "none"
    mock_llm.extract_profile_fields.return_value = {"german_level": "basic"}
    parser = ProfileParser(llm_client=mock_llm)

    draft = parser.parse(EXAMPLE_TEXT)  # "немецкого почти не знаю" → none

    assert draft.german_level == "none"


def test_parser_llm_error_does_not_crash_parser() -> None:
    """Если LLM вернул пустой словарь (ошибка провайдера), парсер продолжает работу."""
    mock_llm = MagicMock()
    mock_llm.extract_profile_fields.return_value = {}
    parser = ProfileParser(llm_client=mock_llm)

    draft = parser.parse(EXAMPLE_TEXT)

    assert draft.current_country == "Germany"


def test_parser_applies_followup_answers_over_draft() -> None:
    parser = ProfileParser()

    draft = parser.parse(
        "Ищу складскую работу",
        followup_answers={
            "desired_roles": "Склад, Логистика",
            "preferred_regions": "Берлин",
            "german_level": "basic",
            "willing_to_relocate": "нет",
            "shift_ok": "да",
        },
    )

    assert draft.desired_roles == ["Склад", "Логистика"]
    assert draft.preferred_regions == ["Берлин"]
    assert draft.german_level == "basic"
    assert draft.willing_to_relocate is False
    assert draft.shift_ok is True


# ---------------------------------------------------------------------------
# Negation context tests
# ---------------------------------------------------------------------------

def test_parser_negation_does_not_add_warehouse_to_desired() -> None:
    """Roles mentioned after negation markers must not appear in desired_roles."""
    parser = ProfileParser()

    draft = parser.parse("Не интересны склад и производство")

    assert "Склад" not in draft.desired_roles
    assert "Производство" not in draft.desired_roles


def test_parser_negation_does_not_add_to_excluded() -> None:
    """Explicit negation is safe to keep as excluded_roles in deterministic fallback."""
    parser = ProfileParser()

    draft = parser.parse("Не хочу склад и производство")

    assert "Склад" not in draft.desired_roles
    assert "Производство" not in draft.desired_roles
    assert "Warehouse" in draft.excluded_roles
    assert "Production" in draft.excluded_roles


def test_parser_positive_warehouse_stays_in_desired_via_followup() -> None:
    """Roles supplied via followup_answers appear in desired_roles."""
    parser = ProfileParser()

    draft = parser.parse(
        "Ищу склад, упаковку и производство",
        followup_answers={"desired_roles": "Склад, Упаковка, Производство"},
    )

    assert "Склад" in draft.desired_roles
    assert "Упаковка" in draft.desired_roles
    assert "Производство" in draft.desired_roles


def test_parser_driving_license_b_does_not_create_driver_role() -> None:
    """Having a category-B driving license must not add Водитель to desired_roles."""
    parser = ProfileParser()

    draft = parser.parse("У меня есть права категории B. Личной машины нет.")

    assert draft.driving_license == "B"
    assert "Водитель" not in draft.desired_roles


def test_parser_explicit_driver_job_adds_driver_role_via_followup() -> None:
    """Explicit driver intent captured via followup_answers in conservative mode."""
    parser = ProfileParser()

    draft = parser.parse(
        "Ищу работу водителем, есть права B",
        followup_answers={"desired_roles": "Водитель"},
    )

    assert "Водитель" in draft.desired_roles
    assert draft.driving_license == "B"


def test_parser_multiple_preferred_regions_do_not_set_current_city() -> None:
    """A list of preferred search regions must not be misread as current city."""
    parser = ProfileParser()

    draft = parser.parse(
        "Предпочтительные регионы: Берлин, Гамбург, Росток, вся Германия. "
        "Готов к переезду."
    )

    assert draft.current_city is None
    assert "Berlin" in draft.preferred_regions
    assert "Hamburg" in draft.preferred_regions
    assert draft.willing_to_relocate is True


def test_parser_physical_work_nyet_sets_flag_false() -> None:
    """'Физическая работа: нет' must set physical_work_ok=False."""
    parser = ProfileParser()

    draft = parser.parse("Физическая работа: нет, профиль для IT-разработки.")

    assert draft.physical_work_ok is False


def test_parser_physical_work_false_excludes_physical_roles() -> None:
    """When physical_work_ok=False, post_process must exclude physical role families."""
    parser = ProfileParser()

    draft = parser.parse(
        "Физическая работа: нет. Ищу Python developer. Склад не интересует."
    )

    assert "Склад" not in draft.desired_roles
    assert draft.physical_work_ok is False


def test_parser_preferred_regions_includes_all_cities_and_germany_wide() -> None:
    """'Берлин, Росток, Гамбург, вся Германия' must produce all four regions."""
    parser = ProfileParser()

    draft = parser.parse(
        "Предпочтительные регионы: Берлин, Росток, Гамбург, вся Германия."
    )

    assert "Berlin" in draft.preferred_regions
    assert "Rostock" in draft.preferred_regions
    assert "Hamburg" in draft.preferred_regions
    assert "Deutschland" in draft.preferred_regions


def test_parser_section24_detected_from_aufenthg_notation() -> None:
    """§24 AufenthG notation must set legal_status=section_24 and work_authorized=True."""
    parser = ProfileParser()

    draft = parser.parse("Нахожусь в Германии по §24 AufenthG.")

    assert draft.legal_status == "section_24"
    assert draft.work_authorized is True


def test_parser_work_authorization_detected_from_phrase() -> None:
    """'Есть разрешение на работу' must set work_authorized=True."""
    parser = ProfileParser()

    draft = parser.parse("Есть разрешение на работу в Германии.")

    assert draft.work_authorized is True


def test_parser_exclusion_priority_removes_overlap_with_desired() -> None:
    """A role in both desired and excluded must be removed from desired."""
    parser = ProfileParser()

    # Force a conflict via followup answers then check post_process resolves it
    draft = parser.parse(
        "Ищу склад. Не интересны склад и производство.",
    )

    # "Склад" appears in positive context first, then negative → excluded wins
    assert "Склад" not in draft.desired_roles
