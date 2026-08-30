from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.profiles import SearchProfile, UserProfile
from app.services.ai_tools_profile import (
    AI_TOOLS_DESIRED_ROLES,
    AI_TOOLS_PROFILE_NAME,
    AI_TOOLS_PROFILE_NOTES,
    AI_TOOLS_SEARCH_QUERY_TERMS,
)
from app.services.profile_catalog_service import ProfileCatalogService, SearchProfileCreateSpec
from app.services.profile_location_sanitizer import sanitize_preferred_locations
from app.services.search_normalizer import normalize_search_location_for_profile


@dataclass(frozen=True, slots=True)
class AIToolsProfilePreview:
    spec: SearchProfileCreateSpec
    german_level: str | None
    english_level: str | None
    search_modes: tuple[str, ...] = ("germany_local", "remote_worldwide")
    source_scopes: tuple[str, ...] = ("western", "russian")
    russian_sources: tuple[str, ...] = ("hh", "dou_rss", "djinni_rss")


@dataclass(frozen=True, slots=True)
class AIToolsProfileCreationResult:
    profile: SearchProfile | None
    created: bool


class AIToolsProfileService:
    """Preview and create the dedicated AI-tools profile via ProfileCatalogService."""

    def __init__(self, *, catalog_service: ProfileCatalogService | None = None) -> None:
        self._catalog_service = catalog_service or ProfileCatalogService()

    def preview(self, db: Session, *, user_profile_id: int | None = None) -> AIToolsProfilePreview | None:
        user_profile = self._resolve_user_profile(db, user_profile_id=user_profile_id)
        if user_profile is None:
            return None

        reference = self._resolve_reference_profile(db, user_profile_id=user_profile.id)
        preferred_locations = sanitize_preferred_locations(
            reference.preferred_locations if reference is not None else None
        )
        if not preferred_locations and user_profile.country_code == "DE":
            preferred_locations = ["Deutschland"]

        relocation_ready = (
            reference.relocation_ready
            if reference is not None and reference.relocation_ready is not None
            else True
        )
        search_location = normalize_search_location_for_profile(
            preferred_locations,
            relocation_ready=relocation_ready,
        )
        spec = SearchProfileCreateSpec(
            user_profile_id=user_profile.id,
            name=AI_TOOLS_PROFILE_NAME,
            desired_roles=AI_TOOLS_DESIRED_ROLES,
            search_query_terms=AI_TOOLS_SEARCH_QUERY_TERMS,
            preferred_locations=tuple(preferred_locations),
            relocation_ready=relocation_ready,
            no_german_required=True,
            notes=AI_TOOLS_PROFILE_NOTES,
            search_query_de=AI_TOOLS_SEARCH_QUERY_TERMS[0],
            search_location_de=search_location,
        )
        return AIToolsProfilePreview(
            spec=spec,
            german_level=user_profile.german_level,
            english_level=user_profile.english_level,
        )

    def create_from_preview(
        self,
        db: Session,
        *,
        preview: AIToolsProfilePreview,
    ) -> AIToolsProfileCreationResult:
        existing = db.execute(
            select(SearchProfile).where(
                SearchProfile.user_profile_id == preview.spec.user_profile_id,
                SearchProfile.name == preview.spec.name,
            )
        ).scalars().first()
        if existing is not None:
            return AIToolsProfileCreationResult(profile=existing, created=False)
        created = self._catalog_service.create_profile(db, spec=preview.spec)
        return AIToolsProfileCreationResult(profile=created, created=created is not None)

    @staticmethod
    def _resolve_user_profile(db: Session, *, user_profile_id: int | None) -> UserProfile | None:
        if user_profile_id is not None:
            return db.get(UserProfile, user_profile_id)
        return db.execute(select(UserProfile).order_by(UserProfile.id.asc())).scalars().first()

    @staticmethod
    def _resolve_reference_profile(db: Session, *, user_profile_id: int) -> SearchProfile | None:
        default = db.execute(
            select(SearchProfile).where(
                SearchProfile.user_profile_id == user_profile_id,
                SearchProfile.is_default.is_(True),
            )
        ).scalars().first()
        if default is not None:
            return default
        return db.execute(
            select(SearchProfile)
            .where(
                SearchProfile.user_profile_id == user_profile_id,
                SearchProfile.is_active.is_(True),
            )
            .order_by(SearchProfile.id.asc())
        ).scalars().first()
