from __future__ import annotations

from datetime import UTC, datetime

from app.db.models.crm import InterviewEvent, LeadEvent
from app.services.application_tracker import ApplicationTrackerService
from app.services.cover_letter_service import CoverLetterService
from app.services.resume_service import ResumeService
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_application_tracker_marks_applied_and_links_documents(
    db_session: Session,
    owner_records: dict[str, object],
    saved_lead,
) -> None:
    user_profile = owner_records["user_profile"]
    resume_service = ResumeService()
    template_service = CoverLetterService()
    tracker = ApplicationTrackerService()

    resume = resume_service.create_resume(db_session, user_profile_id=user_profile.id, title="Немецкое резюме", language="de")
    version = resume_service.create_version(
        db_session,
        resume_id=resume.id,
        version_label="warehouse-v1",
        file_path="C:\\resume\\warehouse-v1.pdf",
        is_default=True,
    )
    template = template_service.create_template(
        db_session,
        user_profile_id=user_profile.id,
        name="Складской отклик",
        template_body="Добрый день, готов начать сразу.",
        language="ru",
    )

    applied_at = datetime(2026, 4, 17, 10, 0, tzinfo=UTC)
    tracker.mark_applied(
        db_session,
        lead=saved_lead,
        applied_at=applied_at,
        application_channel="BA",
        salary_expectation_text="2400 EUR brutto",
        contact_person="Frau Becker",
        contact_email="hr@example.de",
        follow_up_due_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        resume_version_id=version.id,
        cover_letter_template_id=template.id,
    )
    db_session.commit()

    lead_events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc())).scalars()
    )

    assert saved_lead.status == "applied"
    assert saved_lead.applied_at == applied_at
    assert saved_lead.application_channel == "BA"
    assert saved_lead.salary_expectation_text == "2400 EUR brutto"
    assert saved_lead.contact_person == "Frau Becker"
    assert saved_lead.contact_email == "hr@example.de"
    assert saved_lead.resume_version_id == version.id
    assert saved_lead.cover_letter_template_id == template.id
    assert saved_lead.follow_up_due_at == datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
    assert lead_events[-1].event_type == "applied"
    assert lead_events[-1].payload == {
        "application_channel": "BA",
        "salary_expectation_text": "2400 EUR brutto",
        "contact_person": "Frau Becker",
        "contact_email": "hr@example.de",
        "follow_up_due_at": "2026-04-22T09:00:00+00:00",
        "resume_version_id": version.id,
        "cover_letter_template_id": template.id,
    }


def test_application_tracker_handles_reply_rejection_interview_and_archive(db_session: Session, saved_lead) -> None:
    tracker = ApplicationTrackerService()

    tracker.schedule_interview(
        db_session,
        lead=saved_lead,
        interview_at=datetime(2026, 4, 20, 13, 30, tzinfo=UTC),
        interview_format="Zoom",
        interviewer_name="Herr Klein",
        location="Zoom link",
        notes="Подготовить вопросы по сменам.",
    )
    tracker.mark_reply_received(
        db_session,
        lead=saved_lead,
        reply_at=datetime(2026, 4, 18, 8, 0, tzinfo=UTC),
        details="Позвали на короткий созвон и уточнили документы.",
    )
    tracker.mark_rejected(
        db_session,
        lead=saved_lead,
        rejected_at=datetime(2026, 4, 25, 15, 0, tzinfo=UTC),
        reason="Нужен уверенный немецкий.",
    )
    tracker.archive_lead(
        db_session,
        lead=saved_lead,
        archived_at=datetime(2026, 4, 26, 9, 0, tzinfo=UTC),
        note="Закрыли после отказа.",
    )
    db_session.commit()

    interviews = tuple(
        db_session.execute(select(InterviewEvent).where(InterviewEvent.lead_id == saved_lead.id)).scalars()
    )
    events = tuple(
        db_session.execute(select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc())).scalars()
    )

    assert len(interviews) == 1
    assert interviews[0].interview_format == "Zoom"
    assert interviews[0].interviewer_name == "Herr Klein"
    assert saved_lead.reply_at == datetime(2026, 4, 18, 8, 0, tzinfo=UTC)
    assert saved_lead.rejected_at == datetime(2026, 4, 25, 15, 0, tzinfo=UTC)
    assert saved_lead.archived_at == datetime(2026, 4, 26, 9, 0, tzinfo=UTC)
    assert saved_lead.status == "archived"
    assert [event.event_type for event in events[-4:]] == [
        "interview_scheduled",
        "reply_received",
        "rejected",
        "archived",
    ]


def test_application_tracker_preserves_existing_next_action_without_follow_up_due_at(
    db_session: Session,
    saved_lead,
) -> None:
    tracker = ApplicationTrackerService()
    existing_next_action = datetime(2026, 4, 24, 9, 45, tzinfo=UTC)
    existing_follow_up_due = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
    saved_lead.next_action_at = existing_next_action
    saved_lead.follow_up_due_at = existing_follow_up_due
    db_session.commit()

    tracker.mark_applied(
        db_session,
        lead=saved_lead,
        applied_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        application_channel="BA",
    )
    db_session.commit()

    assert saved_lead.follow_up_due_at == existing_follow_up_due
    assert saved_lead.next_action_at == existing_next_action
