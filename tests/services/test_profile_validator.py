from app.services.intake_models import IntakeProfileDraft
from app.services.profile_validator import ProfileValidator


def test_validator_marks_missing_critical_fields_deterministically() -> None:
    validator = ProfileValidator()

    result = validator.validate(IntakeProfileDraft())

    # shift_ok is not asked when desired_roles is empty (roles asked first)
    assert set(result.missing_critical_fields) == {
        "desired_roles",
        "preferred_regions",
        "willing_to_relocate",
        "german_level",
        "work_authorized",
    }


def test_validator_requires_preferred_regions_if_missing_in_germany_case() -> None:
    validator = ProfileValidator()

    result = validator.validate(
        IntakeProfileDraft(
            current_country="Germany",
            desired_roles=["Склад"],
            german_level="basic",
            willing_to_relocate=True,
            shift_ok=True,
            work_authorized=True,
        )
    )

    assert "preferred_regions" in result.missing_critical_fields
    assert not any("регион" in warning.lower() for warning in result.consistency_warnings)


def test_validator_applies_section24_work_authorization_rule() -> None:
    validator = ProfileValidator()

    result = validator.validate(
        IntakeProfileDraft(
            legal_status="section_24",
            desired_roles=["Склад"],
            preferred_regions=["Вся Германия"],
            current_country="Germany",
            german_level="none",
            willing_to_relocate=True,
            shift_ok=True,
        )
    )

    assert result.normalized_draft.work_authorized is True
    assert "work_authorized" not in result.missing_critical_fields


def test_validator_adds_consistency_warning_for_car_without_license() -> None:
    validator = ProfileValidator()

    result = validator.validate(
        IntakeProfileDraft(
            desired_roles=["Склад"],
            preferred_regions=["Вся Германия"],
            current_country="Germany",
            german_level="basic",
            willing_to_relocate=True,
            shift_ok=True,
            has_car=True,
            driving_license="none",
            work_authorized=True,
        )
    )

    assert any("машина" in warning for warning in result.consistency_warnings)
