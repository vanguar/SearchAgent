from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TimestampMixin

FEEDBACK_LABELS: frozenset[str] = frozenset({"relevant", "weak", "irrelevant"})


class RelevanceFeedback(TimestampMixin, Base):
    """Per-profile relevance feedback for a canonical vacancy snapshot."""

    __tablename__ = "relevance_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Canonical vacancy reference — best available dedup key at feedback time
    canonical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(120))
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Human-readable snapshot captured at feedback time
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    location_text: Mapped[str | None] = mapped_column(String(255))
    # Role family classification at feedback time (may be None if unclassified)
    role_family: Mapped[str | None] = mapped_column(String(120))
    current_query: Mapped[str | None] = mapped_column(String(512))
    # "relevant" | "weak" | "irrelevant"
    feedback_label: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "profile_id", "canonical_key",
            name="uq_relevance_feedback_profile_canonical",
        ),
        Index("ix_relevance_feedback_profile_id", "profile_id"),
    )
