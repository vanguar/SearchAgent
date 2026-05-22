from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class ApplicationLeadSchema(ORMBaseSchema):
    id: int
    user_profile_id: int
    search_profile_id: int | None = None
    vacancy_source_record_id: int | None = None
    vacancy_canonical_id: int | None = None
    resume_version_id: int | None = None
    cover_letter_template_id: int | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_external_id: str | None = None
    canonical_key: str | None = None
    vacancy_title: str
    translated_title_ru: str | None = None
    company_name: str | None = None
    location_text: str | None = None
    summary_ru: str | None = None
    original_url: str | None = None
    found_at: datetime | None = None
    viewed_at: datetime | None = None
    opened_original_at: datetime | None = None
    saved_at: datetime | None = None
    applied_at: datetime | None = None
    reply_at: datetime | None = None
    rejected_at: datetime | None = None
    interview_at: datetime | None = None
    archived_at: datetime | None = None
    next_action_at: datetime | None = None
    application_channel: str | None = None
    salary_expectation_text: str | None = None
    contact_person: str | None = None
    contact_email: str | None = None
    follow_up_due_at: datetime | None = None
    follow_up_sent_at: datetime | None = None
    status: str | None = None
    created_at: datetime
    updated_at: datetime


class LeadEventSchema(ORMBaseSchema):
    id: int
    lead_id: int
    event_type: str
    event_at: datetime
    payload: dict | None = None
    created_at: datetime


class ManualNoteSchema(ORMBaseSchema):
    id: int
    user_profile_id: int | None = None
    search_profile_id: int | None = None
    lead_id: int | None = None
    body: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


class ReminderTaskSchema(ORMBaseSchema):
    id: int
    user_profile_id: int | None = None
    lead_id: int | None = None
    title: str
    details: str | None = None
    due_at: datetime | None = None
    next_action_at: datetime | None = None
    completed_at: datetime | None = None
    is_done: bool
    created_at: datetime
    updated_at: datetime


class InterviewEventSchema(ORMBaseSchema):
    id: int
    lead_id: int
    interview_at: datetime
    interviewer_name: str | None = None
    interview_format: str | None = None
    location: str | None = None
    status: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
