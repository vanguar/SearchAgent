from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class VacancyScoreSchema(ORMBaseSchema):
    id: int
    vacancy_canonical_id: int
    search_profile_id: int
    score_total: float
    bucket: str | None = None
    explanation: str | None = None
    scored_at: datetime


class SearchRunSchema(ORMBaseSchema):
    id: int
    search_profile_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    total_fetched: int
    total_new: int
    total_updated: int
    total_scored: int
    notes: str | None = None
