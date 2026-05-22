from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TimestampMixin


class ApplicationLead(TimestampMixin, Base):
    __tablename__ = "application_leads"
    __table_args__ = (
        UniqueConstraint(
            "user_profile_id",
            "source_id",
            "source_external_id",
            name="uq_application_leads_user_source_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    search_profile_id: Mapped[int | None] = mapped_column(ForeignKey("search_profiles.id", ondelete="SET NULL"))
    vacancy_source_record_id: Mapped[int | None] = mapped_column(ForeignKey("vacancy_source_records.id", ondelete="SET NULL"))
    vacancy_canonical_id: Mapped[int | None] = mapped_column(ForeignKey("vacancy_canonicals.id", ondelete="SET NULL"))
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id", ondelete="SET NULL"))
    cover_letter_template_id: Mapped[int | None] = mapped_column(ForeignKey("cover_letter_templates.id", ondelete="SET NULL"))
    source_id: Mapped[str | None] = mapped_column(String(64))
    source_name: Mapped[str | None] = mapped_column(String(64))
    source_external_id: Mapped[str | None] = mapped_column(String(255))
    canonical_key: Mapped[str | None] = mapped_column(String(255))
    vacancy_title: Mapped[str] = mapped_column(String(255), nullable=False)
    translated_title_ru: Mapped[str | None] = mapped_column(String(255))
    company_name: Mapped[str | None] = mapped_column(String(255))
    location_text: Mapped[str | None] = mapped_column(String(255))
    summary_ru: Mapped[str | None] = mapped_column(Text)
    original_url: Mapped[str | None] = mapped_column(String(2048))
    found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_original_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_channel: Mapped[str | None] = mapped_column(String(64))
    salary_expectation_text: Mapped[str | None] = mapped_column(String(255))
    contact_person: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    follow_up_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(32))


class LeadEvent(Base):
    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("application_leads.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ManualNote(TimestampMixin, Base):
    __tablename__ = "manual_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int | None] = mapped_column(ForeignKey("user_profiles.id", ondelete="SET NULL"))
    search_profile_id: Mapped[int | None] = mapped_column(ForeignKey("search_profiles.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("application_leads.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReminderTask(TimestampMixin, Base):
    __tablename__ = "reminder_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int | None] = mapped_column(ForeignKey("user_profiles.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("application_leads.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class InterviewEvent(TimestampMixin, Base):
    __tablename__ = "interview_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("application_leads.id", ondelete="CASCADE"), nullable=False)
    interview_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interviewer_name: Mapped[str | None] = mapped_column(String(255))
    interview_format: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)


Index("ix_application_leads_status", ApplicationLead.status)
Index("ix_application_leads_next_action_at", ApplicationLead.next_action_at)
Index("ix_application_leads_canonical_key", ApplicationLead.user_profile_id, ApplicationLead.canonical_key)
Index("ix_lead_events_lead_id", LeadEvent.lead_id)
Index("ix_reminder_tasks_due_at", ReminderTask.due_at)
Index("ix_interview_events_interview_at", InterviewEvent.interview_at)
