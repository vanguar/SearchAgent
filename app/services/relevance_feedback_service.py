from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.db.models.feedback import RelevanceFeedback

FeedbackLabel = Literal["relevant", "weak", "irrelevant"]

_VALID_LABELS: frozenset[str] = frozenset({"relevant", "weak", "irrelevant"})


@dataclass(frozen=True)
class FeedbackRecord:
    id: int
    profile_id: int
    canonical_key: str
    source_id: str | None
    source_name: str
    normalized_title: str
    company_name: str | None
    location_text: str | None
    role_family: str | None
    current_query: str | None
    feedback_label: str
    created_at: datetime
    updated_at: datetime


class RelevanceFeedbackService:
    """Capture and retrieve per-profile relevance feedback for vacancies.

    One feedback record per (profile_id, canonical_key) pair — upserts on repeat.
    """

    def record_feedback(
        self,
        db: Session,
        *,
        profile_id: int,
        canonical_key: str,
        source_id: str | None = None,
        source_name: str,
        normalized_title: str,
        company_name: str | None = None,
        location_text: str | None = None,
        role_family: str | None = None,
        current_query: str | None = None,
        feedback_label: str,
    ) -> FeedbackRecord:
        """Record or update feedback. Raises ValueError for invalid label."""
        if feedback_label not in _VALID_LABELS:
            raise ValueError(
                f"Invalid feedback_label: {feedback_label!r}. "
                f"Must be one of: {sorted(_VALID_LABELS)}"
            )

        existing = (
            db.query(RelevanceFeedback)
            .filter_by(profile_id=profile_id, canonical_key=canonical_key)
            .first()
        )
        if existing is not None:
            existing.feedback_label = feedback_label
            existing.source_id = source_id
            existing.source_name = source_name
            existing.normalized_title = normalized_title
            existing.company_name = company_name
            existing.location_text = location_text
            existing.role_family = role_family
            existing.current_query = current_query
            db.flush()
            return _to_record(existing)

        row = RelevanceFeedback(
            profile_id=profile_id,
            canonical_key=canonical_key,
            source_id=source_id,
            source_name=source_name,
            normalized_title=normalized_title,
            company_name=company_name,
            location_text=location_text,
            role_family=role_family,
            current_query=current_query,
            feedback_label=feedback_label,
        )
        db.add(row)
        db.flush()
        return _to_record(row)

    def get_feedback(
        self, db: Session, *, profile_id: int, canonical_key: str
    ) -> FeedbackRecord | None:
        row = (
            db.query(RelevanceFeedback)
            .filter_by(profile_id=profile_id, canonical_key=canonical_key)
            .first()
        )
        return _to_record(row) if row is not None else None

    def list_profile_feedback(
        self, db: Session, *, profile_id: int, limit: int = 50
    ) -> list[FeedbackRecord]:
        rows = (
            db.query(RelevanceFeedback)
            .filter_by(profile_id=profile_id)
            .order_by(RelevanceFeedback.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [_to_record(r) for r in rows]

    def delete_feedback(
        self, db: Session, *, feedback_id: int, profile_id: int
    ) -> bool:
        row = (
            db.query(RelevanceFeedback)
            .filter_by(id=feedback_id, profile_id=profile_id)
            .first()
        )
        if row is None:
            return False
        db.delete(row)
        db.flush()
        return True


def _to_record(row: RelevanceFeedback) -> FeedbackRecord:
    return FeedbackRecord(
        id=row.id,
        profile_id=row.profile_id,
        canonical_key=row.canonical_key,
        source_id=row.source_id,
        source_name=row.source_name,
        normalized_title=row.normalized_title,
        company_name=row.company_name,
        location_text=row.location_text,
        role_family=row.role_family,
        current_query=row.current_query,
        feedback_label=row.feedback_label,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
