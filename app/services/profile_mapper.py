from __future__ import annotations

from app.services.intake_models import IntakeProfileDraft, MappedProfilePayload, SearchProfilePayload, UserProfilePayload


def _to_country_code(current_country: str | None) -> str | None:
    if not current_country:
        return None

    normalized = current_country.strip().lower()
    if normalized in {"germany", "германия", "германии", "deutschland", "de"}:
        return "DE"
    return None


def _generate_profile_name(desired_roles: list[str] | None) -> str:
    """Генерируем название профиля из желаемых ролей. Берём первые две, капитализируем."""
    if not desired_roles:
        return "Основной поиск"
    normalized = [r.strip().capitalize() for r in desired_roles if r.strip()]
    if not normalized:
        return "Основной поиск"
    return " / ".join(normalized[:2])


def map_to_profile_payload(draft: IntakeProfileDraft, *, raw_profile_text: str, display_name: str = "Основной профиль") -> MappedProfilePayload:
    """Map intake draft into payload aligned with existing Phase 2 profile models."""
    user_payload = UserProfilePayload(
        display_name=display_name,
        country_code=_to_country_code(draft.current_country),
        city=draft.current_city,
        legal_status=draft.legal_status,
        work_authorized=True if draft.work_authorized is None else draft.work_authorized,
        german_level=draft.german_level,
        english_level=draft.english_level,
        raw_profile_text=raw_profile_text.strip() or None,
    )

    search_payload = SearchProfilePayload(
        name=_generate_profile_name(draft.desired_roles),
        desired_roles=draft.desired_roles or None,
        excluded_roles=draft.excluded_roles or None,
        preferred_locations=draft.preferred_regions or None,
        relocation_ready=draft.willing_to_relocate,
        shift_ok=draft.shift_ok,
        physical_work_ok=draft.physical_work_ok,
        housing_needed=draft.housing_needed,
        start_availability_text=draft.start_availability,
        driver_license=draft.driving_license,
        car_available=draft.has_car,
        no_german_required=False,
    )

    return MappedProfilePayload(user_profile=user_payload, search_profile=search_payload)
