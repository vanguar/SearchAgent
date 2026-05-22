from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class VacancyCanonicalSchema(ORMBaseSchema):
    id: int
    normalized_title: str
    company_name: str | None = None
    location_text: str | None = None
    country_code: str | None = None
    city: str | None = None
    salary_text: str | None = None
    employment_type: str | None = None
    work_model: str | None = None
    description_text: str | None = None
    source_posted_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class VacancySourceRecordSchema(ORMBaseSchema):
    id: int
    canonical_id: int | None = None
    source_name: str
    external_id: str
    source_url: str | None = None
    fetched_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    payload_hash: str | None = None
    raw_payload: dict | None = None
    created_at: datetime
    updated_at: datetime
