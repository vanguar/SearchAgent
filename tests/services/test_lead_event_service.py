from __future__ import annotations

from datetime import UTC, datetime

from app.db.models.crm import LeadEvent
from app.services.lead_event_service import LeadEventService
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_lead_event_service_tracks_view_open_and_idempotent_saved(db_session: Session, saved_lead) -> None:
    service = LeadEventService()
    viewed_at = datetime(2026, 4, 17, 9, 0, tzinfo=UTC)
    opened_at = datetime(2026, 4, 17, 9, 5, tzinfo=UTC)

    viewed_result = service.record_event(
        db_session,
        saved_lead,
        "viewed",
        event_at=viewed_at,
        payload={"vacancy_title": saved_lead.vacancy_title},
    )
    duplicate_saved = service.record_event(
        db_session,
        saved_lead,
        "saved",
        event_at=datetime(2026, 4, 17, 9, 1, tzinfo=UTC),
    )
    opened_result = service.record_event(
        db_session,
        saved_lead,
        "opened_original_link",
        event_at=opened_at,
        payload={"original_url": saved_lead.original_url},
        allow_repeat=True,
    )
    db_session.commit()

    events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc())).scalars()
    )

    assert viewed_result.created is True
    assert duplicate_saved.created is False
    assert opened_result.created is True
    assert saved_lead.viewed_at == viewed_at
    assert saved_lead.opened_original_at == opened_at
    assert [event.event_type for event in events] == ["found", "saved", "viewed", "opened_original_link"]


def test_lead_event_service_builds_timeline_in_reverse_chronological_order(db_session: Session, saved_lead) -> None:
    service = LeadEventService()
    service.record_event(
        db_session,
        saved_lead,
        "opened_original_link",
        event_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        payload={"original_url": "https://example.org/jobs/10000-1234567890-S"},
        allow_repeat=True,
    )
    service.record_event(
        db_session,
        saved_lead,
        "reminder_added",
        event_at=datetime(2026, 4, 18, 8, 30, tzinfo=UTC),
        payload={"title": "Написать фоллоу-ап", "due_at": "2026-04-18T08:30:00+00:00"},
        allow_repeat=True,
    )
    db_session.commit()

    timeline = service.build_timeline(db_session, lead_id=saved_lead.id)

    assert [entry.event_type for entry in timeline[:3]] == ["reminder_added", "opened_original_link", "saved"]
    assert timeline[0].label_ru == "Добавлено напоминание"
    assert "Написать фоллоу-ап" in (timeline[0].details_ru or "")
    assert "https://example.org/jobs/10000-1234567890-S" in (timeline[1].details_ru or "")
