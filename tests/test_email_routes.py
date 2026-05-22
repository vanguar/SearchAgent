from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.crm import LeadEvent
from app.db.models.email import EmailMessage
from app.db.models.email import EmailThread
from app.main import create_app
from app.services.email.base import EmailNotice, EmailWorkspaceData, GmailClientState, ManualEmailIngestResult
from app.web.routes.email import get_email_workspace_service, get_manual_email_ingest_service


class _StubWorkspaceService:
    def build_workspace(self, session, *, selected_thread_id, notice, profile_id=None):  # noqa: ANN001
        _ = session
        _ = selected_thread_id
        _ = notice
        _ = profile_id
        return EmailWorkspaceData(
            owner_label=None,
            warning_message="База CRM сейчас недоступна. Email workspace открыт в деградированном режиме.",
            notice=EmailNotice(kind="warning", message_ru="Деградация email workspace."),
            degraded=True,
            can_import=False,
            threads=(),
            selected_thread=None,
            lead_options=(),
            gmail_state=GmailClientState(
                mode="missing_config",
                configured=False,
                available=False,
                account_email=None,
                message_ru="Gmail пока не настроен.",
                missing_fields=("GMAIL_ACCOUNT_EMAIL",),
            ),
        )


class _StubIngestService:
    def build_input(self, form_data):  # noqa: ANN001
        return form_data

    def ingest(self, session, *, payload, known_user_emails):  # noqa: ANN001
        _ = session
        _ = payload
        _ = known_user_emails
        return ManualEmailIngestResult(
            status="db_unavailable",
            notice_kind="warning",
            notice_message_ru="База CRM временно недоступна. Ручной импорт письма не сохранен.",
            commit_required=False,
        )


def test_email_page_renders_empty_state_and_gmail_not_configured(client, owner_records) -> None:
    _ = owner_records

    response = client.get("/email")

    assert response.status_code == 200
    assert "Цепочки переписки" in response.text
    assert "Пока нет импортированных цепочек" in response.text
    assert "Gmail пока не настроен" in response.text


def test_email_import_and_thread_link_flow(client, db_session, owner_records, saved_lead) -> None:
    _ = owner_records
    saved_lead.contact_email = "hr@example.de"
    db_session.commit()

    imported = client.post(
        "/email/import",
        data={
            "subject": "Re: Lagermitarbeiter Berlin",
            "from_email": "HR Team <hr@example.de>",
            "to_emails": "me@gmail.com",
            "body_text": "Please review https://example.org/jobs/10000-1234567890-S",
            "sent_at": "2026-04-18T10:15",
        },
        follow_redirects=True,
    )
    thread = db_session.execute(select(EmailThread).order_by(EmailThread.id.desc())).scalars().first()

    assert imported.status_code == 200
    assert "Письмо импортировано" in imported.text
    assert thread is not None

    linked = client.post(
        f"/email/threads/{thread.id}/link",
        data={"lead_id": str(saved_lead.id)},
        follow_redirects=True,
    )
    linked_again = client.post(
        f"/email/threads/{thread.id}/link",
        data={"lead_id": str(saved_lead.id)},
        follow_redirects=True,
    )
    events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc()))
        .scalars()
    )

    assert linked.status_code == 200
    assert linked_again.status_code == 200
    assert "Цепочка вручную привязана к лиду." in linked.text
    assert "Цепочка уже привязана к этому лиду." in linked_again.text
    assert (saved_lead.translated_title_ru or saved_lead.vacancy_title) in linked.text
    assert [event.event_type for event in events if event.event_type == "email_thread_linked"] == ["email_thread_linked"]


def test_email_import_route_returns_validation_notice_for_invalid_sent_at(client, owner_records) -> None:
    _ = owner_records

    response = client.post(
        "/email/import",
        data={
            "subject": "Broken datetime",
            "from_email": "hr@example.de",
            "sent_at": "18.04.2026 10:15",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Поле даты отправки заполнено неверно" in response.text


def test_email_import_route_returns_validation_notice_for_invalid_lead_id(client, owner_records) -> None:
    _ = owner_records

    response = client.post(
        "/email/import",
        data={
            "subject": "Bad lead id",
            "from_email": "hr@example.de",
            "lead_id": "abc",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Некорректный ID лида" in response.text


def test_email_thread_link_route_returns_validation_notice_for_invalid_lead_id(
    client,
    db_session,
    owner_records,
) -> None:
    _ = owner_records
    imported = client.post(
        "/email/import",
        data={
            "subject": "Re: Lagermitarbeiter Berlin",
            "from_email": "HR Team <hr@example.de>",
            "body_text": "Message before invalid link.",
        },
        follow_redirects=True,
    )
    thread = db_session.execute(select(EmailThread).order_by(EmailThread.id.desc())).scalars().first()

    assert imported.status_code == 200
    assert thread is not None

    response = client.post(
        f"/email/threads/{thread.id}/link",
        data={"lead_id": "oops"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Некорректный ID лида" in response.text


def test_email_message_unlink_route_handles_invalid_helper_thread_id_without_crashing(client, owner_records) -> None:
    _ = owner_records

    response = client.post(
        "/email/messages/999/unlink",
        data={"thread_id": "oops"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Не удалось отвязать сообщение" in response.text


def test_email_message_unlink_route_does_not_spam_noop_unlink_events(client, db_session, owner_records, saved_lead) -> None:
    _ = owner_records
    imported = client.post(
        "/email/import",
        data={
            "subject": "Re: Lagermitarbeiter Berlin",
            "from_email": "HR Team <hr@example.de>",
            "to_emails": "me@gmail.com",
            "body_text": "Linked message for unlink check.",
            "lead_id": str(saved_lead.id),
            "external_thread_id": "unlink-thread-1",
            "external_message_id": "unlink-message-1",
        },
        follow_redirects=True,
    )
    message = db_session.execute(select(EmailMessage).order_by(EmailMessage.id.desc())).scalars().first()

    assert imported.status_code == 200
    assert message is not None

    first_unlink = client.post(
        f"/email/messages/{message.id}/unlink",
        data={"thread_id": str(message.thread_id)},
        follow_redirects=True,
    )
    second_unlink = client.post(
        f"/email/messages/{message.id}/unlink",
        data={"thread_id": str(message.thread_id)},
        follow_redirects=True,
    )
    events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc()))
        .scalars()
    )

    assert first_unlink.status_code == 200
    assert second_unlink.status_code == 200
    assert "Сообщение отвязано от лида." in first_unlink.text
    assert "Сообщение уже не привязано к лиду." in second_unlink.text
    assert [event.event_type for event in events if event.event_type == "email_message_unlinked"] == [
        "email_message_unlinked"
    ]


def test_email_import_route_handles_db_unavailable_notice() -> None:
    app = create_app()
    app.dependency_overrides[get_manual_email_ingest_service] = lambda: _StubIngestService()
    client = TestClient(app)

    response = client.post(
        "/email/import",
        data={"subject": "Fallback import"},
        follow_redirects=True,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Ручной импорт письма не сохранен" in response.text


def test_email_page_renders_degraded_workspace_stub() -> None:
    app = create_app()
    app.dependency_overrides[get_email_workspace_service] = lambda: _StubWorkspaceService()
    client = TestClient(app)

    response = client.get("/email")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Email workspace открыт в деградированном режиме" in response.text
    assert "Деградация email workspace." in response.text
