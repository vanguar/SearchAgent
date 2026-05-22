from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models.profiles import SearchProfile, UserProfile
from app.db.session import SessionLocal
from app.services.search_models import SearchProfileContext, normalize_profile_text

_CANONICAL_SECTION24_VALUES = frozenset({"section_24", "section24", "section-24"})
_SECTION24_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\d)§\s*24(?!\d)"),
    re.compile(r"\bsection(?:[_\-\s]?24)\b"),
    re.compile(r"\bparagraph(?:[_\-\s]?24)\b"),
    re.compile(r"\bparagraf\w*(?:[_\-\s]?24)\b"),
    re.compile(r"\b24(?:[_\-\s]+paragraf\w*)\b"),
    re.compile(r"\bпараграф\w*(?:[_\-\s]?24)\b"),
    re.compile(r"\b24(?:[_\-\s]+параграф\w*)\b"),
    re.compile(r"\btemporary(?:[_\-\s]+protection)\b"),
)


@dataclass(frozen=True, slots=True)
class LegalStatusInterpretation:
    raw_value: str | None
    normalized_value: str | None
    code: str | None
    section24: bool = False
    work_authorized: bool = False


def interpret_legal_status(value: str | None) -> LegalStatusInterpretation:
    normalized_value = normalize_profile_text(value)
    if not normalized_value:
        return LegalStatusInterpretation(
            raw_value=value,
            normalized_value=None,
            code=None,
        )

    if normalized_value in _CANONICAL_SECTION24_VALUES or any(
        pattern.search(normalized_value) for pattern in _SECTION24_PATTERNS
    ):
        return LegalStatusInterpretation(
            raw_value=value,
            normalized_value=normalized_value,
            code="section_24",
            section24=True,
            work_authorized=True,
        )

    return LegalStatusInterpretation(
        raw_value=value,
        normalized_value=normalized_value,
        code=None,
    )


class DatabaseSearchProfileResolver:
    """Load the saved search profile with explicit legal-status interpretation."""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or SessionLocal

    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        """Resolve a search profile context.

        If profile_id is given, load that specific profile.
        Otherwise, load the default profile (is_default=True), then first active.
        """
        db: Session | None = None
        try:
            db = self._session_factory()
            search_profile = self._load_profile(db, profile_id=profile_id)
            if search_profile is None:
                logger.info(
                    "search_profile_resolved profile_source=fallback reason=no_saved_profile profile_id=%s",
                    profile_id,
                )
                return SearchProfileContext.fallback(
                    note_ru="Сохраненный профиль не найден. Поиск будет использовать безопасный базовый профиль."
                )

            user_profile = db.query(UserProfile).filter(UserProfile.id == search_profile.user_profile_id).first()
            if user_profile is None:
                logger.info("search_profile_resolved profile_source=fallback reason=no_user_profile")
                return SearchProfileContext.fallback(
                    note_ru="Профиль пользователя пока не найден. Поиск будет использовать безопасный базовый профиль."
                )

            legal_status = interpret_legal_status(user_profile.legal_status)
            work_authorized = bool(user_profile.work_authorized or legal_status.work_authorized)

            profile_context = SearchProfileContext(
                profile_label=search_profile.name or user_profile.display_name,
                profile_source="saved",
                note_ru="Используется сохраненный профиль поиска.",
                legal_status=user_profile.legal_status,
                work_authorized=work_authorized,
                german_level=user_profile.german_level,
                english_level=user_profile.english_level,
                desired_roles=tuple(search_profile.desired_roles or ()),
                excluded_roles=tuple(search_profile.excluded_roles or ()),
                preferred_locations=tuple(search_profile.preferred_locations or ()),
                relocation_ready=search_profile.relocation_ready,
                shift_ok=search_profile.shift_ok,
                physical_work_ok=search_profile.physical_work_ok,
                housing_needed=search_profile.housing_needed,
                start_availability_text=search_profile.start_availability_text,
                no_german_required=bool(search_profile.no_german_required),
                section24_interpreted=legal_status.section24,
                search_query_terms=tuple(search_profile.search_query_terms or ()),
            )
            logger.info(
                "search_profile_resolved profile_source=saved search_profile_id=%s user_profile_id=%s",
                search_profile.id,
                user_profile.id,
            )
            return profile_context
        except OperationalError as exc:
            logger.warning(
                "search_profile_resolved profile_source=degraded reason=db_unreachable error=%s",
                _short_db_error(exc),
            )
            return SearchProfileContext.degraded(
                note_ru=(
                    "Сохраненный профиль временно недоступен: нет соединения с базой данных. "
                    "Поиск все равно можно запускать по полям формы; будет использован безопасный базовый профиль."
                )
            )
        except SQLAlchemyError:
            logger.exception("search_profile_resolved profile_source=degraded reason=db_error")
            return SearchProfileContext.degraded(
                note_ru=(
                    "Сохраненный профиль временно недоступен из-за ошибки базы данных. "
                    "Поиск все равно можно запускать по полям формы; будет использован безопасный базовый профиль."
                )
            )
        finally:
            if db is not None:
                db.close()


    def _load_profile(self, db: Session, *, profile_id: int | None) -> SearchProfile | None:
        if profile_id is not None:
            return db.get(SearchProfile, profile_id)
        # prefer default, then first active
        default = db.query(SearchProfile).filter(SearchProfile.is_default.is_(True)).first()
        if default is not None:
            return default
        return (
            db.query(SearchProfile)
            .order_by(SearchProfile.is_active.desc(), SearchProfile.id.asc())
            .first()
        )


def _short_db_error(error: OperationalError) -> str:
    details = str(getattr(error, "orig", error)).strip()
    return details.splitlines()[0] if details else "database operational error"
