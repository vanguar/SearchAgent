from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.crm import ApplicationLead
from app.db.models.crm import LeadEvent, ManualNote
from app.services.lead_service import UNSET, LeadService, SearchLeadCandidate


def test_lead_service_creates_structured_lead_and_initial_events(
    db_session: Session,
    owner_records: dict[str, object],
    lead_candidate: SearchLeadCandidate,
) -> None:
    _ = owner_records
    service = LeadService()

    result = service.create_or_get_from_search_candidate(
        db_session,
        candidate=lead_candidate,
        action="save",
    )
    db_session.commit()

    assert result.created is True
    assert result.duplicate is False
    assert result.lead.source_id == "ba"
    assert result.lead.source_external_id == "10000-1234567890-S"
    assert result.lead.canonical_key == "canonical-warehouse-berlin"
    assert result.lead.vacancy_title == "Lagermitarbeiter/in"
    assert result.lead.translated_title_ru == "Сотрудник склада"
    assert result.lead.original_url == "https://example.org/jobs/10000-1234567890-S"
    assert result.lead.status == "saved"
    assert result.lead.found_at is not None
    assert result.lead.saved_at is not None

    events = tuple(
        db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == result.lead.id).order_by(LeadEvent.id.asc())
        ).scalars()
    )

    assert [event.event_type for event in events] == ["found", "saved"]
    assert events[0].payload == {
        "source_id": "ba",
        "source_name": "BA (Bundesagentur fur Arbeit)",
        "source_external_id": "10000-1234567890-S",
        "canonical_key": "canonical-warehouse-berlin",
        "vacancy_title": "Lagermitarbeiter/in",
        "company_name": "Logistik Nord GmbH",
        "location_text": "Berlin",
        "original_url": "https://example.org/jobs/10000-1234567890-S",
        "translated_title_ru": "Сотрудник склада",
        "summary_ru": "Складская вакансия без высокого языкового барьера.",
        "bucket": "hot",
    }


def test_lead_service_returns_existing_lead_for_duplicate_save_without_new_events(
    db_session: Session,
    owner_records: dict[str, object],
    lead_candidate: SearchLeadCandidate,
) -> None:
    _ = owner_records
    service = LeadService()

    first_result = service.create_or_get_from_search_candidate(
        db_session,
        candidate=lead_candidate,
        action="save",
    )
    second_result = service.create_or_get_from_search_candidate(
        db_session,
        candidate=lead_candidate,
        action="save",
    )
    db_session.commit()

    events = tuple(
        db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == first_result.lead.id).order_by(LeadEvent.id.asc())
        ).scalars()
    )

    assert first_result.lead.id == second_result.lead.id
    assert second_result.created is False
    assert second_result.duplicate is True
    assert [event.event_type for event in events] == ["found", "saved"]


def test_lead_service_recovers_from_source_identity_unique_conflict_and_reuses_existing_lead(
    db_session: Session,
    saved_lead,
    lead_candidate: SearchLeadCandidate,
) -> None:
    class RaceConflictLeadService(LeadService):
        def __init__(self) -> None:
            super().__init__()
            self.lookup_calls = 0

        def _find_existing_lead(
            self,
            session: Session,
            *,
            user_profile_id: int,
            candidate: SearchLeadCandidate,
        ) -> ApplicationLead | None:
            self.lookup_calls += 1
            if self.lookup_calls == 1:
                return None
            return super()._find_existing_lead(
                session,
                user_profile_id=user_profile_id,
                candidate=candidate,
            )

    service = RaceConflictLeadService()

    result = service.create_or_get_from_search_candidate(
        db_session,
        candidate=lead_candidate,
        action="save",
    )
    db_session.commit()

    events = tuple(
        db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc())
        ).scalars()
    )

    assert result.lead.id == saved_lead.id
    assert result.created is False
    assert result.duplicate is True
    assert service.lookup_calls >= 2
    assert [event.event_type for event in events] == ["found", "saved"]


def test_lead_service_add_note_creates_note_and_event(db_session: Session, saved_lead) -> None:
    service = LeadService()

    note = service.add_note(
        db_session,
        lead=saved_lead,
        body="Перезвонить через три дня, если не ответят.",
        pinned=True,
    )
    db_session.commit()

    stored_note = db_session.get(ManualNote, note.id)
    events = tuple(
        db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == saved_lead.id).order_by(LeadEvent.id.asc())
        ).scalars()
    )

    assert stored_note is not None
    assert stored_note.body == "Перезвонить через три дня, если не ответят."
    assert events[-1].event_type == "note_added"
    assert events[-1].payload == {
        "note_id": note.id,
        "body": "Перезвонить через три дня, если не ответят.",
        "pinned": True,
    }


def test_application_lead_source_identity_is_unique_at_db_level(
    db_session: Session,
    owner_records: dict[str, object],
    saved_lead,
) -> None:
    user_profile = owner_records["user_profile"]

    duplicate_lead = ApplicationLead(
        user_profile_id=user_profile.id,
        search_profile_id=saved_lead.search_profile_id,
        source_id=saved_lead.source_id,
        source_name=saved_lead.source_name,
        source_external_id=saved_lead.source_external_id,
        canonical_key="another-canonical-key",
        vacancy_title="Another warehouse role",
        company_name="Duplicate GmbH",
        location_text="Hamburg",
    )
    db_session.add(duplicate_lead)

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()


def test_lead_service_snapshot_sync_preserves_existing_populated_fields_for_sparse_candidate(
    db_session: Session,
    owner_records: dict[str, object],
    lead_candidate: SearchLeadCandidate,
) -> None:
    _ = owner_records
    service = LeadService()
    created = service.create_or_get_from_search_candidate(
        db_session,
        candidate=lead_candidate,
        action="save",
    )

    sparse_candidate = SearchLeadCandidate(
        source_id=lead_candidate.source_id,
        source_name=lead_candidate.source_name,
        source_external_id=lead_candidate.source_external_id,
        vacancy_title=lead_candidate.vacancy_title,
        original_url="   ",
        canonical_key="  ",
        translated_title_ru=None,
        company_name=" ",
        location_text=None,
        summary_ru="   ",
        bucket=lead_candidate.bucket,
    )
    updated = service.create_or_get_from_search_candidate(
        db_session,
        candidate=sparse_candidate,
        action="viewed",
    )
    db_session.commit()

    assert updated.lead.id == created.lead.id
    assert updated.lead.translated_title_ru == "Сотрудник склада"
    assert updated.lead.company_name == "Logistik Nord GmbH"
    assert updated.lead.location_text == "Berlin"
    assert updated.lead.summary_ru == "Складская вакансия без высокого языкового барьера."
    assert updated.lead.original_url == "https://example.org/jobs/10000-1234567890-S"
    assert updated.lead.canonical_key == "canonical-warehouse-berlin"


def test_update_manual_details_preserves_next_action_when_follow_up_due_is_missing(db_session: Session, saved_lead) -> None:
    service = LeadService()
    existing_next_action = datetime(2026, 4, 24, 11, 30, tzinfo=UTC)
    saved_lead.next_action_at = existing_next_action
    saved_lead.follow_up_due_at = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
    db_session.commit()

    service.update_manual_details(
        db_session,
        lead=saved_lead,
        application_channel="BA",
        salary_expectation_text=None,
        contact_person=None,
        contact_email=None,
        follow_up_due_at=UNSET,
        follow_up_sent_at=UNSET,
        next_action_at=UNSET,
    )
    db_session.commit()

    assert saved_lead.next_action_at == existing_next_action
