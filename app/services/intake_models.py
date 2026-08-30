from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class IntakeProfileDraft:
    current_country: str | None = None
    current_city: str | None = None
    legal_status: str | None = None
    work_authorized: bool | None = None
    german_level: str | None = None
    english_level: str | None = None
    desired_roles: list[str] = field(default_factory=list)
    excluded_roles: list[str] = field(default_factory=list)
    preferred_regions: list[str] = field(default_factory=list)
    remote_allowed: bool | None = None
    international_remote_allowed: bool | None = None
    work_modes: list[str] = field(default_factory=list)
    willing_to_relocate: bool | None = None
    shift_ok: bool | None = None
    physical_work_ok: bool | None = None
    housing_needed: bool | None = None
    start_availability: str | None = None
    driving_license: str | None = None
    has_car: bool | None = None
    search_query_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FollowUpQuestion:
    field_name: str
    label: str
    input_type: str
    placeholder: str = ""


@dataclass(frozen=True, slots=True)
class IntakeValidationResult:
    normalized_draft: IntakeProfileDraft
    missing_critical_fields: tuple[str, ...]
    consistency_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UserProfilePayload:
    display_name: str
    country_code: str | None
    city: str | None
    legal_status: str | None
    work_authorized: bool
    german_level: str | None
    english_level: str | None
    raw_profile_text: str | None


@dataclass(frozen=True, slots=True)
class SearchProfilePayload:
    name: str
    desired_roles: list[str] | None
    excluded_roles: list[str] | None
    preferred_locations: list[str] | None
    relocation_ready: bool | None
    shift_ok: bool | None
    physical_work_ok: bool | None
    housing_needed: bool | None
    start_availability_text: str | None
    driver_license: str | None
    car_available: bool | None
    no_german_required: bool = False
    search_query_de: str | None = None
    search_query_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MappedProfilePayload:
    user_profile: UserProfilePayload
    search_profile: SearchProfilePayload


@dataclass(frozen=True, slots=True)
class IntakeAnalysisResult:
    draft: IntakeProfileDraft
    missing_fields: tuple[str, ...]
    questions: tuple[FollowUpQuestion, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntakeSaveResult:
    saved: bool
    message: str
    user_profile_id: int | None = None
    search_profile_id: int | None = None
    mapped_payload: MappedProfilePayload | None = None
