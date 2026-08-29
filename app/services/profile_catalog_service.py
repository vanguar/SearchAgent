from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.search_normalizer import normalize_query_from_roles, normalize_search_location_for_profile


@dataclass(frozen=True, slots=True)
class ProfileCatalogEntry:
    profile: SearchProfile
    user_profile: UserProfile
    is_default: bool


class ProfileCatalogService:
    """Manage the SearchProfile catalog: list, create, duplicate, set-default, edit."""

    def list_profiles(self, db: Session) -> list[ProfileCatalogEntry]:
        try:
            profiles = db.execute(
                select(SearchProfile).order_by(
                    SearchProfile.is_default.desc(),
                    SearchProfile.is_active.desc(),
                    SearchProfile.id.asc(),
                )
            ).scalars().all()
        except SQLAlchemyError:
            logger.warning("profile_catalog_list_failed — likely pending migration")
            return []
        result: list[ProfileCatalogEntry] = []
        for sp in profiles:
            up = db.get(UserProfile, sp.user_profile_id)
            if up is None:
                continue
            result.append(ProfileCatalogEntry(profile=sp, user_profile=up, is_default=sp.is_default))
        return result

    def get_profile(self, db: Session, *, profile_id: int) -> SearchProfile | None:
        return db.get(SearchProfile, profile_id)

    def set_default(self, db: Session, *, profile_id: int) -> bool:
        """Set profile as default; atomically clears all other defaults for the same user."""
        target = db.get(SearchProfile, profile_id)
        if target is None:
            return False
        try:
            # Single transaction: clear others first, then set target.
            db.execute(
                update(SearchProfile)
                .where(
                    SearchProfile.user_profile_id == target.user_profile_id,
                    SearchProfile.id != profile_id,
                )
                .values(is_default=False)
            )
            target.is_default = True
            db.commit()
            logger.info("profile_set_default search_profile_id=%s", profile_id)
            return True
        except SQLAlchemyError:
            db.rollback()
            logger.exception("profile_set_default_failed search_profile_id=%s", profile_id)
            return False

    def duplicate_profile(self, db: Session, *, source_id: int) -> SearchProfile | None:
        """Duplicate a profile with a new name."""
        source = db.get(SearchProfile, source_id)
        if source is None:
            return None
        try:
            copy = SearchProfile(
                user_profile_id=source.user_profile_id,
                name=f"{source.name} (копия)",
                is_active=source.is_active,
                is_default=False,
                desired_roles=list(source.desired_roles) if source.desired_roles else None,
                excluded_roles=list(source.excluded_roles) if source.excluded_roles else None,
                preferred_locations=list(source.preferred_locations) if source.preferred_locations else None,
                relocation_ready=source.relocation_ready,
                shift_ok=source.shift_ok,
                physical_work_ok=source.physical_work_ok,
                housing_needed=source.housing_needed,
                start_availability_text=source.start_availability_text,
                driver_license=source.driver_license,
                car_available=source.car_available,
                no_german_required=source.no_german_required,
                notes=source.notes,
                search_query_terms=list(source.search_query_terms) if source.search_query_terms else None,
                search_query_de=source.search_query_de,
                search_location_de=source.search_location_de,
            )
            db.add(copy)
            db.commit()
            db.refresh(copy)
            logger.info("profile_duplicated source_id=%s new_id=%s", source_id, copy.id)
            return copy
        except SQLAlchemyError:
            db.rollback()
            logger.exception("profile_duplicate_failed source_id=%s", source_id)
            return None

    def update_profile(
        self,
        db: Session,
        *,
        profile_id: int,
        name: str | None = None,
        desired_roles_text: str | None = None,
        preferred_locations_text: str | None = None,
        relocation_ready: bool | None = None,
        shift_ok: bool | None = None,
        physical_work_ok: bool | None = None,
        no_german_required: bool | None = None,
        notes: str | None = None,
    ) -> SearchProfile | None:
        target = db.get(SearchProfile, profile_id)
        if target is None:
            return None
        try:
            if name is not None:
                target.name = name.strip() or target.name
            if desired_roles_text is not None:
                roles = [r.strip() for r in desired_roles_text.split(",") if r.strip()]
                normalized_roles = roles or None
                if list(target.desired_roles or []) != list(normalized_roles or []):
                    target.search_query_terms = None
                target.desired_roles = normalized_roles
                # Пересчитываем нормализованный query при изменении ролей
                target.search_query_de = normalize_query_from_roles(target.desired_roles)
            if preferred_locations_text is not None:
                locs = [loc.strip() for loc in preferred_locations_text.split(",") if loc.strip()]
                target.preferred_locations = locs or None
                # Пересчитываем нормализованную локацию при изменении мест
                target.search_location_de = normalize_search_location_for_profile(
                    target.preferred_locations,
                    relocation_ready=target.relocation_ready,
                )
            if relocation_ready is not None:
                target.relocation_ready = relocation_ready
            if shift_ok is not None:
                target.shift_ok = shift_ok
            if physical_work_ok is not None:
                target.physical_work_ok = physical_work_ok
            if no_german_required is not None:
                target.no_german_required = no_german_required
            if notes is not None:
                target.notes = notes.strip() or None
            db.commit()
            db.refresh(target)
            logger.info("profile_updated search_profile_id=%s", profile_id)
            return target
        except SQLAlchemyError:
            db.rollback()
            logger.exception("profile_update_failed search_profile_id=%s", profile_id)
            return None

    def delete_profile(self, db: Session, *, profile_id: int) -> bool:
        """Delete a profile. If it was the default, promote the next available profile."""
        target = db.get(SearchProfile, profile_id)
        if target is None:
            return False
        try:
            was_default = target.is_default
            user_profile_id = target.user_profile_id
            db.delete(target)
            db.flush()

            if was_default:
                next_profile = db.execute(
                    select(SearchProfile)
                    .where(SearchProfile.user_profile_id == user_profile_id)
                    .order_by(SearchProfile.id.asc())
                ).scalars().first()
                if next_profile is not None:
                    next_profile.is_default = True

            db.commit()
            logger.info("profile_deleted search_profile_id=%s was_default=%s", profile_id, was_default)
            return True
        except SQLAlchemyError:
            db.rollback()
            logger.exception("profile_delete_failed search_profile_id=%s", profile_id)
            return False

    def backfill_search_fields(self, db: Session) -> int:
        """Заполняем search_query_de / search_location_de для профилей, у которых они пусты.
        Использует только статический словарь (без LLM). Возвращает число обновлённых профилей.
        """
        try:
            profiles = db.execute(select(SearchProfile)).scalars().all()
        except SQLAlchemyError:
            logger.warning("backfill_search_fields_failed — likely pending migration")
            return 0

        updated = 0
        for profile in profiles:
            changed = False
            user_profile = db.get(UserProfile, profile.user_profile_id)
            raw_text = (user_profile.raw_profile_text or "").lower() if user_profile is not None else ""
            if _raw_text_indicates_remote_worldwide(raw_text):
                preferred = list(profile.preferred_locations or [])
                for location in ("worldwide remote", "international remote companies"):
                    if location not in preferred:
                        preferred.insert(0, location)
                        changed = True
                profile.preferred_locations = preferred
            if not profile.search_query_de and profile.desired_roles:
                profile.search_query_de = normalize_query_from_roles(profile.desired_roles)
                changed = True
            resolved_location = normalize_search_location_for_profile(
                profile.preferred_locations,
                relocation_ready=profile.relocation_ready,
            )
            if profile.preferred_locations and profile.search_location_de != resolved_location:
                profile.search_location_de = resolved_location
                changed = True
            if changed:
                updated += 1

        if updated:
            try:
                db.commit()
                logger.info("backfill_search_fields updated=%s profiles", updated)
            except SQLAlchemyError:
                db.rollback()
                logger.exception("backfill_search_fields_commit_failed")
                return 0
        return updated

    def resolve_default_profile_id(self, db: Session) -> int | None:
        """Return the id of the default profile, or the first active one, or None."""
        try:
            default = db.execute(
                select(SearchProfile).where(SearchProfile.is_default.is_(True))
            ).scalars().first()
            if default is not None:
                return default.id
            first_active = db.execute(
                select(SearchProfile)
                .where(SearchProfile.is_active.is_(True))
                .order_by(SearchProfile.id.asc())
            ).scalars().first()
            return first_active.id if first_active is not None else None
        except SQLAlchemyError:
            logger.warning("profile_catalog_resolve_default_failed — likely pending migration")
            return None


def _raw_text_indicates_remote_worldwide(raw_text: str) -> bool:
    return any(
        token in raw_text
        for token in (
            "worldwide remote",
            "remote worldwide",
            "по всему миру",
            "international remote",
            "remote-компани",
            "только удал",
        )
    )
