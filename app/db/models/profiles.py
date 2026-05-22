from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TimestampMixin


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(255))
    legal_status: Mapped[str | None] = mapped_column(String(120))
    work_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    german_level: Mapped[str | None] = mapped_column(String(32))
    english_level: Mapped[str | None] = mapped_column(String(32))
    raw_profile_text: Mapped[str | None] = mapped_column(Text)


class SearchProfile(TimestampMixin, Base):
    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    desired_roles: Mapped[list[str] | None] = mapped_column(JSON)
    excluded_roles: Mapped[list[str] | None] = mapped_column(JSON)
    preferred_locations: Mapped[list[str] | None] = mapped_column(JSON)
    relocation_ready: Mapped[bool | None] = mapped_column(Boolean)
    shift_ok: Mapped[bool | None] = mapped_column(Boolean)
    physical_work_ok: Mapped[bool | None] = mapped_column(Boolean)
    housing_needed: Mapped[bool | None] = mapped_column(Boolean)
    start_availability_text: Mapped[str | None] = mapped_column(String(255))
    driver_license: Mapped[str | None] = mapped_column(String(32))
    car_available: Mapped[bool | None] = mapped_column(Boolean)
    no_german_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    search_query_de: Mapped[str | None] = mapped_column(String(512))
    search_location_de: Mapped[str | None] = mapped_column(String(255))
    search_query_terms: Mapped[list[str] | None] = mapped_column(JSON)
