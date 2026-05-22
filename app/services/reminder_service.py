from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.models.crm import ApplicationLead, ReminderTask
from app.services.lead_event_service import LeadEventService


class ReminderService:
    """Manual reminder tasks for follow-ups and next actions."""

    def __init__(self, *, lead_event_service: LeadEventService | None = None) -> None:
        self.lead_event_service = lead_event_service or LeadEventService()

    def create_reminder(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        title: str,
        due_at: datetime | None,
        details: str | None = None,
        event_at: datetime | None = None,
    ) -> ReminderTask:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("У напоминания должен быть заголовок.")

        reminder = ReminderTask(
            user_profile_id=lead.user_profile_id,
            lead_id=lead.id,
            title=normalized_title,
            details=(details or "").strip() or None,
            due_at=due_at,
            next_action_at=due_at,
            is_done=False,
        )
        session.add(reminder)
        session.flush()
        if due_at is not None:
            lead.next_action_at = due_at
        self.lead_event_service.record_event(
            session,
            lead,
            "reminder_added",
            event_at=event_at or due_at or utc_now(),
            payload={
                "reminder_id": reminder.id,
                "title": reminder.title,
                "due_at": reminder.due_at,
                "details": reminder.details,
            },
            allow_repeat=True,
        )
        session.flush()
        return reminder
