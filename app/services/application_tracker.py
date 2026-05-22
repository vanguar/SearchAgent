from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.models.crm import ApplicationLead, InterviewEvent
from app.services.lead_event_service import LeadEventService


@dataclass(frozen=True, slots=True)
class InterviewScheduleResult:
    lead: ApplicationLead
    interview_event: InterviewEvent


class ApplicationTrackerService:
    """Manual-first ATS transitions for applications, replies, rejections, and interviews."""

    def __init__(self, *, lead_event_service: LeadEventService | None = None) -> None:
        self.lead_event_service = lead_event_service or LeadEventService()

    def mark_applied(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        applied_at: datetime | None = None,
        application_channel: str | None = None,
        salary_expectation_text: str | None = None,
        contact_person: str | None = None,
        contact_email: str | None = None,
        follow_up_due_at: datetime | None = None,
        follow_up_sent_at: datetime | None = None,
        resume_version_id: int | None = None,
        cover_letter_template_id: int | None = None,
    ) -> ApplicationLead:
        effective_applied_at = applied_at or utc_now()
        previous_follow_up_due_at = lead.follow_up_due_at
        lead.applied_at = effective_applied_at
        lead.application_channel = application_channel or lead.application_channel
        lead.salary_expectation_text = salary_expectation_text or lead.salary_expectation_text
        lead.contact_person = contact_person or lead.contact_person
        lead.contact_email = contact_email or lead.contact_email
        if follow_up_due_at is not None:
            lead.follow_up_due_at = follow_up_due_at
        if follow_up_sent_at is not None:
            lead.follow_up_sent_at = follow_up_sent_at
        if follow_up_due_at is not None and (
            lead.next_action_at is None or lead.next_action_at == previous_follow_up_due_at
        ):
            lead.next_action_at = follow_up_due_at
        if resume_version_id is not None:
            lead.resume_version_id = resume_version_id
        if cover_letter_template_id is not None:
            lead.cover_letter_template_id = cover_letter_template_id
        self.lead_event_service.record_event(
            session,
            lead,
            "applied",
            event_at=effective_applied_at,
            payload={
                "application_channel": lead.application_channel,
                "salary_expectation_text": lead.salary_expectation_text,
                "contact_person": lead.contact_person,
                "contact_email": lead.contact_email,
                "follow_up_due_at": lead.follow_up_due_at,
                "resume_version_id": lead.resume_version_id,
                "cover_letter_template_id": lead.cover_letter_template_id,
            },
        )
        session.flush()
        return lead

    def mark_reply_received(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        reply_at: datetime | None = None,
        details: str | None = None,
    ) -> ApplicationLead:
        effective_reply_at = reply_at or utc_now()
        lead.reply_at = effective_reply_at
        self.lead_event_service.record_event(
            session,
            lead,
            "reply_received",
            event_at=effective_reply_at,
            payload={"details": details},
        )
        session.flush()
        return lead

    def mark_rejected(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        rejected_at: datetime | None = None,
        reason: str | None = None,
    ) -> ApplicationLead:
        effective_rejected_at = rejected_at or utc_now()
        lead.rejected_at = effective_rejected_at
        lead.next_action_at = None
        self.lead_event_service.record_event(
            session,
            lead,
            "rejected",
            event_at=effective_rejected_at,
            payload={"reason": reason},
        )
        session.flush()
        return lead

    def schedule_interview(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        interview_at: datetime,
        interview_format: str | None = None,
        interviewer_name: str | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> InterviewScheduleResult:
        lead.interview_at = interview_at
        lead.next_action_at = interview_at
        interview = InterviewEvent(
            lead_id=lead.id,
            interview_at=interview_at,
            interviewer_name=interviewer_name,
            interview_format=interview_format,
            location=location,
            status="scheduled",
            notes=notes,
        )
        session.add(interview)
        session.flush()
        self.lead_event_service.record_event(
            session,
            lead,
            "interview_scheduled",
            event_at=interview_at,
            payload={
                "interviewer_name": interviewer_name,
                "interview_format": interview_format,
                "location": location,
                "notes": notes,
                "interview_event_id": interview.id,
            },
        )
        session.flush()
        return InterviewScheduleResult(lead=lead, interview_event=interview)

    def archive_lead(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        archived_at: datetime | None = None,
        note: str | None = None,
    ) -> ApplicationLead:
        effective_archived_at = archived_at or utc_now()
        lead.archived_at = effective_archived_at
        lead.next_action_at = None
        self.lead_event_service.record_event(
            session,
            lead,
            "archived",
            event_at=effective_archived_at,
            payload={"note": note},
        )
        session.flush()
        return lead
