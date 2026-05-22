from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models.email import EmailMessage, EmailThread
from app.main import create_app
from app.services.cover_letter_service import CoverLetterService
from app.services.resume_service import ResumeService
from app.web.routes.leads import get_lead_service


def _candidate_form_data() -> dict[str, str]:
    return {
        "source_id": "ba",
        "source_name": "BA (Bundesagentur fur Arbeit)",
        "source_external_id": "10000-1234567890-S",
        "canonical_key": "canonical-warehouse-berlin",
        "vacancy_title": "Lagermitarbeiter/in",
        "translated_title_ru": "Сотрудник склада",
        "company_name": "Logistik Nord GmbH",
        "location_text": "Berlin",
        "summary_ru": "Складская вакансия без высокого языкового барьера.",
        "bucket": "hot",
        "original_url": "https://example.org/jobs/10000-1234567890-S",
    }


class _FailingOpenOriginalLeadService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def create_or_get_from_search_candidate(self, session, *, candidate, action, profile_id=None):  # noqa: ANN001
        _ = session
        _ = candidate
        _ = action
        _ = profile_id
        raise self.error


class _FailingLeadEventService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def record_event(self, db, lead, event_type, payload, allow_repeat=False):  # noqa: ANN001
        _ = db
        _ = lead
        _ = event_type
        _ = payload
        _ = allow_repeat
        raise self.error


class _LeadServiceForLeadOpenOriginal:
    def __init__(self, *, lead=None, load_error: Exception | None = None, tracking_error: Exception | None = None) -> None:  # noqa: ANN001
        self._lead = lead or SimpleNamespace(
            id=41,
            vacancy_title="Lagermitarbeiter/in",
            original_url="https://example.org/jobs/lead-41",
        )
        self._load_error = load_error
        self.lead_event_service = _FailingLeadEventService(tracking_error or SQLAlchemyError("tracking failed"))

    def get_lead(self, db, *, lead_id):  # noqa: ANN001
        _ = db
        _ = lead_id
        if self._load_error is not None:
            raise self._load_error
        return self._lead


def test_leads_page_renders_dashboard_with_real_leads(client, owner_records, saved_lead) -> None:
    _ = owner_records
    _ = saved_lead

    response = client.get("/leads")

    assert response.status_code == 200
    assert "Всего лидов" in response.text
    assert "Lagermitarbeiter/in" in response.text
    assert "Складская вакансия без высокого языкового барьера." in response.text
    assert "Резюме" in response.text
    assert "Шаблоны сопроводительного" in response.text


def test_save_from_search_route_returns_feedback_and_duplicate_message(client, owner_records) -> None:
    _ = owner_records

    first_response = client.post("/leads/from-search", data={**_candidate_form_data(), "action": "save"})
    second_response = client.post("/leads/from-search", data={**_candidate_form_data(), "action": "save"})

    assert first_response.status_code == 200
    assert "Лид добавлен в отклики." in first_response.text
    assert "Открыть карточку" in first_response.text
    assert second_response.status_code == 200
    assert "Эта вакансия уже была сохранена." in second_response.text


def test_lead_detail_form_flows_render_updated_state(
    client,
    db_session: Session,
    owner_records: dict[str, object],
    saved_lead,
) -> None:
    user_profile = owner_records["user_profile"]
    resume_service = ResumeService()
    template_service = CoverLetterService()

    resume = resume_service.create_resume(db_session, user_profile_id=user_profile.id, title="Немецкое базовое", language="de")
    version = resume_service.create_version(
        db_session,
        resume_id=resume.id,
        version_label="v1",
        file_path="C:\\resume\\de-v1.pdf",
        is_default=True,
    )
    template = template_service.create_template(
        db_session,
        user_profile_id=user_profile.id,
        name="Быстрый отклик",
        template_body="Добрый день, готов начать сразу.",
        language="ru",
    )
    db_session.commit()

    detail_response = client.get(f"/leads/{saved_lead.id}")
    assert detail_response.status_code == 200
    assert "Полная карточка лида" in detail_response.text

    client.post(
        f"/leads/{saved_lead.id}/details",
        data={
            "application_channel": "BA",
            "salary_expectation_text": "2400 EUR brutto",
            "contact_person": "Frau Becker",
            "contact_email": "hr@example.de",
            "follow_up_due_at": "2026-04-22",
            "follow_up_sent_at": "",
            "next_action_at": "2026-04-22T09:00",
            "resume_version_id": str(version.id),
            "cover_letter_template_id": str(template.id),
        },
    )
    client.post(f"/leads/{saved_lead.id}/apply", data={"applied_at": "2026-04-17"})
    client.post(
        f"/leads/{saved_lead.id}/interview",
        data={
            "interview_at": "2026-04-20T13:30",
            "interview_format": "Zoom",
            "interviewer_name": "Herr Klein",
            "interview_location": "Zoom link",
            "interview_notes": "Подготовить вопросы про смены.",
        },
    )
    client.post(
        f"/leads/{saved_lead.id}/reminders",
        data={
            "reminder_title": "Написать фоллоу-ап",
            "reminder_due_at": "2026-04-22T09:00",
            "reminder_details": "Если молчат, спросить про статус.",
        },
    )
    client.post(
        f"/leads/{saved_lead.id}/notes",
        data={"note_body": "Уточнить жилье и график.", "note_pinned": "on"},
    )

    refreshed = client.get(f"/leads/{saved_lead.id}")

    assert refreshed.status_code == 200
    assert "Frau Becker" in refreshed.text
    assert "hr@example.de" in refreshed.text
    assert "Немецкое базовое · v1" in refreshed.text
    assert "Быстрый отклик" in refreshed.text
    assert "Написать фоллоу-ап" in refreshed.text
    assert "Уточнить жилье и график." in refreshed.text
    assert "Zoom" in refreshed.text
    assert "Отклик отправлен" in refreshed.text


def test_apply_route_keeps_next_action_when_follow_up_due_is_not_submitted(client, db_session: Session, saved_lead) -> None:
    client.post(
        f"/leads/{saved_lead.id}/details",
        data={
            "application_channel": "BA",
            "salary_expectation_text": "",
            "contact_person": "",
            "contact_email": "",
            "follow_up_due_at": "2026-04-24",
            "follow_up_sent_at": "",
            "next_action_at": "2026-04-24T11:00",
            "resume_version_id": "",
            "cover_letter_template_id": "",
        },
    )

    response = client.post(
        f"/leads/{saved_lead.id}/apply",
        data={"applied_at": "2026-04-17"},
        follow_redirects=True,
    )
    db_session.refresh(saved_lead)

    assert response.status_code == 200
    assert "24.04.2026 11:00" in response.text
    assert saved_lead.next_action_at is not None
    assert saved_lead.next_action_at.strftime("%Y-%m-%dT%H:%M") == "2026-04-24T11:00"


def test_open_original_route_redirects_even_when_owner_context_is_unavailable() -> None:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _FailingOpenOriginalLeadService(
        ValueError("Сначала сохраните профиль пользователя, чтобы вести отклики.")
    )
    client = TestClient(app)

    response = client.post(
        "/leads/open-original",
        data=_candidate_form_data(),
        follow_redirects=False,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "https://example.org/jobs/10000-1234567890-S"


def test_open_original_route_redirects_even_when_tracking_db_is_unavailable() -> None:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _FailingOpenOriginalLeadService(
        SQLAlchemyError("database unavailable")
    )
    client = TestClient(app)

    response = client.post(
        "/leads/open-original",
        data=_candidate_form_data(),
        follow_redirects=False,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "https://example.org/jobs/10000-1234567890-S"


def test_open_original_route_returns_explicit_error_when_link_is_missing() -> None:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _FailingOpenOriginalLeadService(
        ValueError("Сначала сохраните профиль пользователя, чтобы вести отклики.")
    )
    client = TestClient(app)

    candidate_data = _candidate_form_data()
    candidate_data["original_url"] = ""
    response = client.post(
        "/leads/open-original",
        data=candidate_data,
        follow_redirects=False,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Не удалось открыть оригинал вакансии" in response.text


def test_open_original_from_lead_route_redirects_when_tracking_write_fails() -> None:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _LeadServiceForLeadOpenOriginal()
    client = TestClient(app)

    response = client.post(
        "/leads/41/open-original",
        data={"original_url": "https://example.org/jobs/lead-41"},
        follow_redirects=False,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "https://example.org/jobs/lead-41"


def test_open_original_from_lead_route_redirects_when_lead_loading_fails_but_form_has_url() -> None:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _LeadServiceForLeadOpenOriginal(
        load_error=SQLAlchemyError("database unavailable")
    )
    client = TestClient(app)

    response = client.post(
        "/leads/41/open-original",
        data={"original_url": "https://example.org/jobs/lead-41"},
        follow_redirects=False,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "https://example.org/jobs/lead-41"


def test_open_original_from_lead_route_returns_explicit_error_when_lead_loading_fails_without_url() -> None:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _LeadServiceForLeadOpenOriginal(
        load_error=SQLAlchemyError("database unavailable")
    )
    client = TestClient(app)

    response = client.post(
        "/leads/41/open-original",
        data={},
        follow_redirects=False,
    )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "Не удалось открыть оригинал вакансии" in response.text


def test_lead_detail_shows_linked_email_summary(client, db_session: Session, saved_lead) -> None:
    thread = EmailThread(
        user_profile_id=saved_lead.user_profile_id,
        lead_id=saved_lead.id,
        external_thread_id="thread-linked-1",
        subject="Re: Lagermitarbeiter Berlin",
        participant_emails=["hr@example.de", "me@gmail.com"],
        last_message_at=datetime(2026, 4, 18, 10, 30, tzinfo=UTC),
    )
    db_session.add(thread)
    db_session.flush()
    db_session.add(
        EmailMessage(
            thread_id=thread.id,
            lead_id=saved_lead.id,
            external_message_id="message-linked-1",
            from_email="hr@example.de",
            to_emails=["me@gmail.com"],
            cc_emails=[],
            bcc_emails=[],
            subject="Re: Lagermitarbeiter Berlin",
            body_text="Short reply from HR.",
            sent_at=datetime(2026, 4, 18, 10, 30, tzinfo=UTC),
            is_incoming=True,
            raw_payload={"import_source": "manual"},
        )
    )
    db_session.commit()

    response = client.get(f"/leads/{saved_lead.id}")

    assert response.status_code == 200
    assert "Связанная переписка" in response.text
    assert "Re: Lagermitarbeiter Berlin" in response.text
    assert f"/email?thread_id={thread.id}" in response.text
