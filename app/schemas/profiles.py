from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class UserProfileSchema(ORMBaseSchema):
    id: int
    display_name: str
    country_code: str | None = None
    city: str | None = None
    legal_status: str | None = None
    work_authorized: bool
    german_level: str | None = None
    english_level: str | None = None
    raw_profile_text: str | None = None
    created_at: datetime
    updated_at: datetime


class SearchProfileSchema(ORMBaseSchema):
    id: int
    user_profile_id: int
    name: str
    is_active: bool
    desired_roles: list[str] | None = None
    excluded_roles: list[str] | None = None
    preferred_locations: list[str] | None = None
    relocation_ready: bool | None = None
    shift_ok: bool | None = None
    physical_work_ok: bool | None = None
    housing_needed: bool | None = None
    start_availability_text: str | None = None
    driver_license: str | None = None
    car_available: bool | None = None
    no_german_required: bool = False
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
