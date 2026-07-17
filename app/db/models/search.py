from __future__ import annotations

from datetime import datetime

from app.db.base import Base
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column


class VacancyScore(Base):
    __tablename__ = "vacancy_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vacancy_canonical_id: Mapped[int] = mapped_column(ForeignKey("vacancy_canonicals.id", ondelete="CASCADE"), nullable=False)
    search_profile_id: Mapped[int] = mapped_column(ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=False)
    score_total: Mapped[float] = mapped_column(Float, nullable=False)
    bucket: Mapped[str | None] = mapped_column(String(32))
    explanation: Mapped[str | None] = mapped_column(Text)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_profile_id: Mapped[int] = mapped_column(ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    total_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_scored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


Index("ix_vacancy_scores_search_profile_id", VacancyScore.search_profile_id)
Index("ix_search_runs_search_profile_id", SearchRun.search_profile_id)
