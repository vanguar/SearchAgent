from __future__ import annotations

from app.services.intake_models import IntakeProfileDraft, IntakeValidationResult
from app.services.profile_location_sanitizer import sanitize_preferred_locations
from app.services.profile_role_taxonomy import classify_roles, is_it_profile, requires_shift_question

CRITICAL_FIELDS: tuple[str, ...] = (
    "desired_roles",
    "preferred_regions",
    "willing_to_relocate",
    "german_level",
    "shift_ok",
)


class ProfileValidator:
    """Deterministic validation and completeness checks for intake draft."""

    def validate(self, draft: IntakeProfileDraft) -> IntakeValidationResult:
        normalized = self._normalize(draft)
        missing_fields = self._detect_missing_critical_fields(normalized)
        warnings = self._detect_consistency_warnings(normalized)

        return IntakeValidationResult(
            normalized_draft=normalized,
            missing_critical_fields=missing_fields,
            consistency_warnings=warnings,
        )

    def _normalize(self, draft: IntakeProfileDraft) -> IntakeProfileDraft:
        normalized = IntakeProfileDraft(
            current_country=self._normalize_country(draft.current_country),
            current_city=self._normalize_text(draft.current_city),
            legal_status=self._normalize_legal_status(draft.legal_status),
            work_authorized=draft.work_authorized,
            german_level=self._normalize_language_level(draft.german_level),
            english_level=self._normalize_language_level(draft.english_level),
            desired_roles=self._normalize_list(draft.desired_roles),
            excluded_roles=self._normalize_list(draft.excluded_roles),
            preferred_regions=sanitize_preferred_locations(draft.preferred_regions),
            remote_allowed=draft.remote_allowed,
            international_remote_allowed=draft.international_remote_allowed,
            work_modes=self._normalize_list(draft.work_modes),
            willing_to_relocate=draft.willing_to_relocate,
            shift_ok=draft.shift_ok,
            physical_work_ok=draft.physical_work_ok,
            housing_needed=draft.housing_needed,
            start_availability=self._normalize_text(draft.start_availability),
            driving_license=self._normalize_driving_license(draft.driving_license),
            has_car=draft.has_car,
            search_query_terms=list(draft.search_query_terms),
        )

        if normalized.legal_status == "section_24":
            normalized.work_authorized = True

        return normalized

    @staticmethod
    def _normalize_country(country: str | None) -> str | None:
        if not country:
            return None

        value = country.strip().lower()
        if value in {"германия", "германии", "germany", "deutschland", "de"}:
            return "Germany"
        return country.strip().title()

    @staticmethod
    def _normalize_text(value: str | None) -> str | None:
        if not value:
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _normalize_legal_status(value: str | None) -> str | None:
        if not value:
            return None

        normalized = value.strip().lower()
        if normalized in {"section_24", "24", "§24", "paragraph_24"}:
            return "section_24"
        return normalized

    @staticmethod
    def _normalize_language_level(value: str | None) -> str | None:
        if not value:
            return None

        normalized = value.strip().lower()
        mapping = {
            "a1": "basic",
            "a2": "basic",
            "b1": "intermediate",
            "b2": "advanced",
            "c1": "advanced",
            "c2": "advanced",
            "none": "none",
            "нет": "none",
            "zero": "none",
            "basic": "basic",
            "базовый": "basic",
            "intermediate": "intermediate",
            "advanced": "advanced",
        }
        return mapping.get(normalized, normalized)

    @staticmethod
    def _normalize_list(values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @staticmethod
    def _normalize_driving_license(value: str | None) -> str | None:
        if not value:
            return None

        normalized = value.strip().upper()
        if normalized in {"NONE", "NO"}:
            return "none"
        if normalized == "YES":
            return "yes"
        return normalized

    @staticmethod
    def _detect_missing_critical_fields(draft: IntakeProfileDraft) -> tuple[str, ...]:
        missing: list[str] = []

        if not draft.desired_roles:
            missing.append("desired_roles")

        if not draft.preferred_regions and draft.remote_allowed is not True:
            missing.append("preferred_regions")

        if draft.willing_to_relocate is None:
            missing.append("willing_to_relocate")

        if draft.german_level is None:
            missing.append("german_level")

        # shift_ok criticality depends on role families via taxonomy
        if draft.shift_ok is None:
            families = classify_roles(draft.desired_roles)
            if requires_shift_question(families):
                missing.append("shift_ok")
            elif not is_it_profile(families) and draft.desired_roles:
                # Unknown family with actual roles → ask about shifts
                missing.append("shift_ok")
            # IT profile OR empty roles (no roles yet → will be asked first) → skip shift_ok

        if draft.work_authorized is None and draft.legal_status != "section_24":
            missing.append("work_authorized")

        return tuple(missing)

    @staticmethod
    def _detect_consistency_warnings(draft: IntakeProfileDraft) -> tuple[str, ...]:
        warnings: list[str] = []

        if draft.has_car is True and draft.driving_license == "none":
            warnings.append("Указано, что есть машина, но права отмечены как отсутствующие.")

        return tuple(warnings)
