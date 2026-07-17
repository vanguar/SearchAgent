from __future__ import annotations

from app.db.models.crm import LeadEvent
from app.db.models.email import EmailMessage, EmailThread
from app.services.email.manual_email_ingest import ManualEmailIngestService
from sqlalchemy import select


def test_manual_email_ingest_persists_message_and_builds_match(db_session, owner_records, saved_lead) -> None:
    _ = owner_records
    saved_lead.contact_email = "hr@example.de"
    db_session.commit()

    service = ManualEmailIngestService()
    result = service.ingest(
        db_session,
        payload=service.build_input(
            {
                "subject": "Re: Lagermitarbeiter Berlin",
                "from_email": "HR Team <hr@example.de>",
                "to_emails": "me@gmail.com",
                "body_text": "Please reply regarding https://example.org/jobs/10000-1234567890-S",
                "sent_at": "2026-04-18T10:15",
            }
        ),
        known_user_emails=("me@gmail.com",),
    )
    if result.commit_required:
        db_session.commit()

    thread = db_session.execute(select(EmailThread).order_by(EmailThread.id.desc())).scalars().first()
    message = db_session.execute(select(EmailMessage).order_by(EmailMessage.id.desc())).scalars().first()

    assert result.status == "imported"
    assert result.match_result.status in {"exact", "strong"}
    assert thread is not None
    assert thread.subject == "Lagermitarbeiter Berlin"
    assert message is not None
    assert message.from_email == "hr@example.de"
    assert message.raw_payload["import_source"] == "manual"


def test_manual_email_ingest_can_link_to_explicit_lead(db_session, saved_lead) -> None:
    service = ManualEmailIngestService()
    result = service.ingest(
        db_session,
        payload=service.build_input(
            {
                "subject": "Re: Lagermitarbeiter Berlin",
                "from_email": "HR Team <hr@example.de>",
                "to_emails": "me@gmail.com",
                "body_text": "Manual link for existing lead.",
                "lead_id": str(saved_lead.id),
            }
        ),
        known_user_emails=("me@gmail.com",),
    )
    if result.commit_required:
        db_session.commit()

    thread = db_session.execute(select(EmailThread).order_by(EmailThread.id.desc())).scalars().first()
    message = db_session.execute(select(EmailMessage).order_by(EmailMessage.id.desc())).scalars().first()

    assert result.status == "imported"
    assert result.linked_lead_id == saved_lead.id
    assert thread is not None and thread.lead_id == saved_lead.id
    assert message is not None and message.lead_id == saved_lead.id


def test_manual_email_ingest_returns_owner_missing_without_profile(db_session) -> None:
    service = ManualEmailIngestService()
    result = service.ingest(
        db_session,
        payload=service.build_input(
            {
                "subject": "Manual import without owner",
                "from_email": "hr@example.de",
                "body_text": "No profile in DB.",
            }
        ),
        known_user_emails=(),
    )

    assert result.status == "owner_missing"
    assert result.commit_required is False


def test_manual_email_ingest_returns_validation_error_for_invalid_sent_at(db_session, owner_records) -> None:
    _ = owner_records
    service = ManualEmailIngestService()

    result = service.ingest(
        db_session,
        payload=service.build_input(
            {
                "subject": "Некорректная дата",
                "from_email": "hr@example.de",
                "body_text": "Дата сломана.",
                "sent_at": "18.04.2026 10:15",
            }
        ),
        known_user_emails=(),
    )

    assert result.status == "validation_error"
    assert "Поле даты отправки заполнено неверно" in result.notice_message_ru
    assert db_session.execute(select(EmailThread)).scalars().first() is None


def test_manual_email_ingest_returns_validation_error_for_too_long_external_id(db_session, owner_records) -> None:
    _ = owner_records
    service = ManualEmailIngestService()

    result = service.ingest(
        db_session,
        payload=service.build_input(
            {
                "subject": "Слишком длинный внешний ID",
                "from_email": "hr@example.de",
                "external_message_id": "x" * 300,
            }
        ),
        known_user_emails=(),
    )

    assert result.status == "validation_error"
    assert "Внешний ID письма слишком длинный" in result.notice_message_ru
    assert db_session.execute(select(EmailThread)).scalars().first() is None


def test_manual_email_ingest_reimport_keeps_email_link_events_idempotent(db_session, saved_lead) -> None:
    service = ManualEmailIngestService()
    payload = {
        "subject": "Re: Lagermitarbeiter Berlin",
        "from_email": "HR Team <hr@example.de>",
        "to_emails": "me@gmail.com",
        "body_text": "Повторный импорт того же письма.",
        "external_thread_id": "manual-thread-100",
        "external_message_id": "manual-message-100",
        "lead_id": str(saved_lead.id),
    }

    first_result = service.ingest(
        db_session,
        payload=service.build_input(payload),
        known_user_emails=("me@gmail.com",),
    )
    if first_result.commit_required:
        db_session.commit()

    second_result = service.ingest(
        db_session,
        payload=service.build_input(payload),
        known_user_emails=("me@gmail.com",),
    )
    if second_result.commit_required:
        db_session.commit()

    email_events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc()))
        .scalars()
    )

    assert first_result.status == "imported"
    assert second_result.status == "imported"
    assert [event.event_type for event in email_events if event.event_type.startswith("email_")] == [
        "email_thread_linked",
        "email_message_linked",
    ]
