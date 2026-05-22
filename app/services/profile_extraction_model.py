"""Structured output model for Profile Intake LLM extraction.

This is the canonical intermediate representation between free text and the
database. LLM fills it; post-processor normalises it; validator checks it.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    return []


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "да", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "нет", "n", "off"}:
            return False
    return None


class ProfileExtractionResult(BaseModel):
    # ── Location ──────────────────────────────────────────────────────────────
    current_country: str | None = None
    current_city: str | None = None          # ONLY when explicitly stated as residence
    current_location_raw: str | None = None  # Raw phrase from text, e.g. "Трибзес"

    # ── Legal / authorization ─────────────────────────────────────────────────
    legal_status: str | None = None          # section_24 | eu_citizen | work_visa | other
    work_authorization: bool | None = None

    # ── Languages ─────────────────────────────────────────────────────────────
    german_level: str | None = None          # none | basic | intermediate | advanced
    english_level: str | None = None
    native_languages: list[str] = Field(default_factory=list)

    # ── Roles ─────────────────────────────────────────────────────────────────
    desired_roles: list[str] = Field(default_factory=list)
    excluded_roles: list[str] = Field(default_factory=list)
    desired_role_families: list[str] = Field(default_factory=list)
    excluded_role_families: list[str] = Field(default_factory=list)

    # ── Search geography ──────────────────────────────────────────────────────
    preferred_regions: list[str] = Field(default_factory=list)  # official German names
    remote_allowed: bool | None = None
    international_remote_allowed: bool | None = None
    relocation_ready: bool | None = None

    # ── Work conditions ───────────────────────────────────────────────────────
    work_modes: list[str] = Field(default_factory=list)   # remote | hybrid | office
    employment_types: list[str] = Field(default_factory=list)  # full-time | part-time | project-based | freelance | contract
    shift_work_allowed: bool | None = None
    physical_work_allowed: bool | None = None
    housing_needed: bool | None = None

    # ── Availability ──────────────────────────────────────────────────────────
    availability: str | None = None  # "immediately" | "from May" | etc.

    # ── Equipment / documents ─────────────────────────────────────────────────
    driving_license: str | None = None  # B | yes | none
    has_car: bool | None = None

    # ── Skills ───────────────────────────────────────────────────────────────
    core_stack: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    project_types: list[str] = Field(default_factory=list)

    # ── Filters ───────────────────────────────────────────────────────────────
    hard_filters: list[str] = Field(default_factory=list)   # must-exclude
    soft_preferences: list[str] = Field(default_factory=list)

    # ── Search terms (for downstream job search) ──────────────────────────────
    search_query_terms: list[str] = Field(default_factory=list)
    negative_query_terms: list[str] = Field(default_factory=list)

    # ── Meta ──────────────────────────────────────────────────────────────────
    profile_summary: str | None = None
    questions_needed: list[str] = Field(default_factory=list)
    confidence_by_field: dict[str, float] = Field(default_factory=dict)
    evidence_by_field: dict[str, str | None] = Field(default_factory=dict)

    @field_validator(
        "native_languages",
        "desired_roles",
        "excluded_roles",
        "desired_role_families",
        "excluded_role_families",
        "preferred_regions",
        "work_modes",
        "employment_types",
        "core_stack",
        "tools",
        "project_types",
        "hard_filters",
        "soft_preferences",
        "search_query_terms",
        "negative_query_terms",
        "questions_needed",
        mode="before",
    )
    @classmethod
    def _validate_string_lists(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)

    @field_validator(
        "work_authorization",
        "remote_allowed",
        "international_remote_allowed",
        "relocation_ready",
        "shift_work_allowed",
        "physical_work_allowed",
        "housing_needed",
        "has_car",
        mode="before",
    )
    @classmethod
    def _validate_optional_bools(cls, value: Any) -> bool | None:
        return _coerce_optional_bool(value)

    @field_validator("confidence_by_field", mode="before")
    @classmethod
    def _validate_confidence(cls, value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, float] = {}
        for key, raw in value.items():
            if raw in (None, ""):
                continue
            try:
                cleaned[str(key)] = float(raw)
            except (TypeError, ValueError):
                continue
        return cleaned

    @field_validator("evidence_by_field", mode="before")
    @classmethod
    def _validate_evidence(cls, value: Any) -> dict[str, str | None]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): None if raw in (None, "") else str(raw)
            for key, raw in value.items()
        }
