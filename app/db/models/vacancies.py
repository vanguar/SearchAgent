from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TimestampMixin


class VacancyCanonical(TimestampMixin, Base):
    __tablename__ = "vacancy_canonicals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_title: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    location_text: Mapped[str | None] = mapped_column(String(255))
    country_code: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(255))
    salary_text: Mapped[str | None] = mapped_column(String(255))
    employment_type: Mapped[str | None] = mapped_column(String(80))
    work_model: Mapped[str | None] = mapped_column(String(80))
    description_text: Mapped[str | None] = mapped_column(Text)
    source_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VacancySourceRecord(TimestampMixin, Base):
    __tablename__ = "vacancy_source_records"
    __table_args__ = (UniqueConstraint("source_name", "external_id", name="uq_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[int | None] = mapped_column(ForeignKey("vacancy_canonicals.id", ondelete="SET NULL"))
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_hash: Mapped[str | None] = mapped_column(String(128))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)


Index("ix_vacancy_source_records_canonical_id", VacancySourceRecord.canonical_id)
Index("ix_vacancy_source_records_last_seen_at", VacancySourceRecord.last_seen_at)
Index("ix_vacancy_canonicals_last_seen_at", VacancyCanonical.last_seen_at)
