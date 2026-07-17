from __future__ import annotations

from datetime import UTC, datetime

from app.db.models.crm import LeadEvent, ReminderTask
from app.services.reminder_service import ReminderService
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_reminder_service_creates_reminder_task_and_event(db_session: Session, saved_lead) -> None:
    service = ReminderService()
    due_at = datetime(2026, 4, 21, 9, 15, tzinfo=UTC)

    reminder = service.create_reminder(
        db_session,
        lead=saved_lead,
        title="Написать фоллоу-ап",
        due_at=due_at,
        details="Если ответа нет, спросить про статус отклика.",
    )
    db_session.commit()

    stored_reminder = db_session.get(ReminderTask, reminder.id)
    events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc())).scalars()
    )

    assert stored_reminder is not None
    assert stored_reminder.title == "Написать фоллоу-ап"
    assert stored_reminder.due_at == due_at
    assert saved_lead.next_action_at == due_at
    assert events[-1].event_type == "reminder_added"
    assert events[-1].payload == {
        "reminder_id": reminder.id,
        "title": "Написать фоллоу-ап",
        "due_at": "2026-04-21T09:15:00+00:00",
        "details": "Если ответа нет, спросить про статус отклика.",
    }


def test_reminder_service_without_due_at_preserves_existing_next_action(db_session: Session, saved_lead) -> None:
    service = ReminderService()
    existing_next_action = datetime(2026, 4, 25, 10, 30, tzinfo=UTC)
    saved_lead.next_action_at = existing_next_action
    db_session.commit()

    reminder = service.create_reminder(
        db_session,
        lead=saved_lead,
        title="Уточнить статус отклика",
        due_at=None,
        details="Проверить, не пришел ли ответ на почту.",
    )
    db_session.commit()

    assert reminder.due_at is None
    assert saved_lead.next_action_at == existing_next_action
