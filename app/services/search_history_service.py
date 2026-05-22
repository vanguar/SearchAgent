from __future__ import annotations
from collections.abc import Callable
from datetime import UTC, date, datetime, time

from sqlalchemy import and_, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.core.time import utc_now
from app.db.models.profiles import SearchProfile
from app.db.models.search import SearchRun, VacancyScore
from app.db.models.vacancies import VacancyCanonical, VacancySourceRecord
from app.db.session import SessionLocal
from app.services.search_models import SearchResultItem, SearchRunResult


class SearchHistoryService:
    """Persist accepted PHASE 7 search results into existing search-history entities."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._now_provider = now_provider or utc_now

    def record_search_result(self, *, result: SearchRunResult, profile_id: int | None = None) -> None:
        if result.profile.profile_source == "degraded":
            logger.info(
                "search_history_persistence_skipped reason=profile_unavailable profile_source=%s",
                result.profile.profile_source,
            )
            return

        session: Session | None = None
        try:
            session = self._session_factory()
            search_run = self.persist_search_result(session, result=result, profile_id=profile_id)
            if search_run is None:
                logger.info(
                    "search_history_persistence_skipped reason=no_active_search_profile profile_source=%s",
                    result.profile.profile_source,
                )
                session.rollback()
                return
            session.commit()
            logger.info(
                "search_history_persisted search_profile_id=%s total_scored=%s total_new=%s total_updated=%s",
                search_run.search_profile_id,
                search_run.total_scored,
                search_run.total_new,
                search_run.total_updated,
            )
        except OperationalError as exc:
            logger.warning(
                "search_history_persistence_skipped reason=db_unreachable error=%s",
                _short_db_error(exc),
            )
            if session is not None:
                session.rollback()
        except SQLAlchemyError:
            logger.exception("search_history_record_failed")
            if session is not None:
                session.rollback()
        finally:
            if session is not None:
                session.close()

    def persist_search_result(
        self, session: Session, *, result: SearchRunResult, profile_id: int | None = None
    ) -> SearchRun | None:
        if profile_id is not None:
            search_profile = session.get(SearchProfile, profile_id)
        else:
            search_profile = session.execute(
                select(SearchProfile)
                .order_by(SearchProfile.is_default.desc(), SearchProfile.is_active.desc(), SearchProfile.id.asc())
            ).scalars().first()
        if search_profile is None:
            return None

        observed_at = self._now_provider()
        search_run = SearchRun(
            search_profile_id=search_profile.id,
            started_at=observed_at,
            finished_at=observed_at,
            status="completed",
            total_fetched=result.total_raw_records,
            total_scored=result.total_canonical_results,
            notes=(
                f"hot={len(result.hot_results)} "
                f"maybe={len(result.maybe_results)} "
                f"rejected={len(result.rejected_results)}"
            ),
        )
        session.add(search_run)
        session.flush()

        new_canonical_count = 0
        updated_canonical_count = 0

        for item in result.results:
            canonical, created = self._upsert_canonical(session, item=item, observed_at=observed_at)
            if created:
                new_canonical_count += 1
            else:
                updated_canonical_count += 1
            self._upsert_source_records(session, item=item, canonical=canonical, observed_at=observed_at)
            session.add(
                VacancyScore(
                    vacancy_canonical_id=canonical.id,
                    search_profile_id=search_profile.id,
                    score_total=float(item.score_result.score),
                    bucket=item.bucket,
                    explanation=item.explanation_ru,
                    scored_at=observed_at,
                )
            )

        search_run.total_new = new_canonical_count
        search_run.total_updated = updated_canonical_count
        session.flush()
        return search_run

    def _upsert_canonical(
        self,
        session: Session,
        *,
        item: SearchResultItem,
        observed_at: datetime,
    ) -> tuple[VacancyCanonical, bool]:
        matched_source_records = self._find_existing_source_records(session, item=item)
        existing_canonical_ids = sorted(
            {
                record.canonical_id
                for record in matched_source_records
                if record.canonical_id is not None
            }
        )

        created = False
        if existing_canonical_ids:
            canonical = session.get(VacancyCanonical, existing_canonical_ids[0])
            if canonical is None:
                canonical = self._build_canonical(item=item, observed_at=observed_at)
                session.add(canonical)
                session.flush()
                created = True
        else:
            canonical = self._build_canonical(item=item, observed_at=observed_at)
            session.add(canonical)
            session.flush()
            created = True

        primary_record = item.primary_record
        canonical.normalized_title = item.canonical_group.normalized_title
        canonical.company_name = item.canonical_group.company_name
        canonical.location_text = item.canonical_group.location_text
        canonical.country_code = item.canonical_group.country_code
        canonical.city = item.canonical_group.city
        canonical.source_posted_at = _date_to_datetime(item.canonical_group.posted_date)
        canonical.first_seen_at = canonical.first_seen_at or observed_at
        canonical.last_seen_at = observed_at
        canonical.description_text = _pick_best_description(item)
        canonical.salary_text = canonical.salary_text or _extract_short_text(primary_record.raw_payload, "salary")
        canonical.employment_type = canonical.employment_type or _extract_short_text(
            primary_record.raw_payload,
            "employment_type",
        )
        canonical.work_model = canonical.work_model or _extract_short_text(primary_record.raw_payload, "work_model")

        for extra_record in matched_source_records:
            extra_record.canonical_id = canonical.id

        session.flush()
        return canonical, created

    def _build_canonical(self, *, item: SearchResultItem, observed_at: datetime) -> VacancyCanonical:
        return VacancyCanonical(
            normalized_title=item.canonical_group.normalized_title,
            company_name=item.canonical_group.company_name,
            location_text=item.canonical_group.location_text,
            country_code=item.canonical_group.country_code,
            city=item.canonical_group.city,
            description_text=_pick_best_description(item),
            source_posted_at=_date_to_datetime(item.canonical_group.posted_date),
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )

    def _upsert_source_records(
        self,
        session: Session,
        *,
        item: SearchResultItem,
        canonical: VacancyCanonical,
        observed_at: datetime,
    ) -> None:
        for source_record in item.canonical_group.source_records:
            record = session.execute(
                select(VacancySourceRecord)
                .where(
                    and_(
                        VacancySourceRecord.source_name == source_record.source_name,
                        VacancySourceRecord.external_id == source_record.external_id,
                    )
                )
                .order_by(VacancySourceRecord.id.asc())
            ).scalars().first()

            if record is None:
                record = VacancySourceRecord(
                    canonical_id=canonical.id,
                    source_name=source_record.source_name,
                    external_id=source_record.external_id,
                    first_seen_at=observed_at,
                )
                session.add(record)

            record.canonical_id = canonical.id
            record.source_url = source_record.source_url
            record.fetched_at = observed_at
            record.last_seen_at = observed_at
            record.first_seen_at = record.first_seen_at or observed_at
            record.raw_payload = source_record.raw_payload

        session.flush()

    def _find_existing_source_records(
        self,
        session: Session,
        *,
        item: SearchResultItem,
    ) -> tuple[VacancySourceRecord, ...]:
        matched_records: list[VacancySourceRecord] = []
        for source_record in item.canonical_group.source_records:
            record = session.execute(
                select(VacancySourceRecord)
                .where(
                    and_(
                        VacancySourceRecord.source_name == source_record.source_name,
                        VacancySourceRecord.external_id == source_record.external_id,
                    )
                )
                .order_by(VacancySourceRecord.id.asc())
            ).scalars().first()
            if record is not None:
                matched_records.append(record)
        return tuple(matched_records)


def _pick_best_description(item: SearchResultItem) -> str | None:
    body_candidates = [
        (source_record.body_text or "").strip()
        for source_record in item.canonical_group.source_records
    ]
    body_candidates = [candidate for candidate in body_candidates if candidate]
    if not body_candidates:
        return item.summary_ru
    return max(body_candidates, key=len)


def _date_to_datetime(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _extract_short_text(raw_payload: object, key: str) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    value = raw_payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _short_db_error(error: OperationalError) -> str:
    details = str(getattr(error, "orig", error)).strip()
    return details.splitlines()[0] if details else "database operational error"
