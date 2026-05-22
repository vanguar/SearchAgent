from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.models.crm import ApplicationLead, InterviewEvent, ManualNote, ReminderTask
from app.db.models.documents import CoverLetterTemplate, Resume, ResumeVersion
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.lead_event_service import LeadEventService, LeadTimelineEntry, status_label

UNSET: Final[object] = object()


@dataclass(frozen=True, slots=True)
class LeadOwnerContext:
    user_profile_id: int
    search_profile_id: int | None
    profile_label: str


@dataclass(frozen=True, slots=True)
class SearchLeadCandidate:
    source_id: str
    source_name: str
    source_external_id: str
    vacancy_title: str
    original_url: str | None = None
    canonical_key: str | None = None
    translated_title_ru: str | None = None
    company_name: str | None = None
    location_text: str | None = None
    summary_ru: str | None = None
    bucket: str | None = None

    def found_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_external_id": self.source_external_id,
            "canonical_key": self.canonical_key,
            "vacancy_title": self.vacancy_title,
            "company_name": self.company_name,
            "location_text": self.location_text,
            "original_url": self.original_url,
            "translated_title_ru": self.translated_title_ru,
            "summary_ru": self.summary_ru,
            "bucket": self.bucket,
        }


@dataclass(frozen=True, slots=True)
class LeadUpsertResult:
    lead: ApplicationLead
    created: bool
    duplicate: bool


@dataclass(frozen=True, slots=True)
class LeadCardView:
    lead: ApplicationLead
    status_label_ru: str
    linked_resume_label: str | None
    linked_template_name: str | None
    latest_note: str | None
    follow_up_due: bool


@dataclass(frozen=True, slots=True)
class LeadDashboardSummary:
    total_leads: int
    active_leads: int
    applied_leads: int
    due_follow_ups: int
    scheduled_interviews: int


@dataclass(frozen=True, slots=True)
class LeadDashboardData:
    owner_context: LeadOwnerContext | None
    summary: LeadDashboardSummary
    leads: tuple[LeadCardView, ...]
    reminders: tuple[ReminderTask, ...]
    warning_message: str | None = None


@dataclass(frozen=True, slots=True)
class LeadDetailData:
    lead: ApplicationLead
    status_label_ru: str
    notes: tuple[ManualNote, ...]
    reminders: tuple[ReminderTask, ...]
    interviews: tuple[InterviewEvent, ...]
    timeline: tuple[LeadTimelineEntry, ...]
    linked_resume_label: str | None
    linked_template_name: str | None


class LeadService:
    """Lead creation, deduplication, manual notes, and page-oriented read models."""

    def __init__(self, *, lead_event_service: LeadEventService | None = None) -> None:
        self.lead_event_service = lead_event_service or LeadEventService()

    def resolve_owner_context(
        self, session: Session, *, profile_id: int | None = None
    ) -> LeadOwnerContext | None:
        if profile_id is not None:
            search_profile = session.get(SearchProfile, profile_id)
        else:
            search_profile = session.execute(
                select(SearchProfile)
                .order_by(SearchProfile.is_default.desc(), SearchProfile.is_active.desc(), SearchProfile.id.asc())
            ).scalars().first()
        if search_profile is not None:
            user_profile = session.get(UserProfile, search_profile.user_profile_id)
            if user_profile is not None:
                return LeadOwnerContext(
                    user_profile_id=user_profile.id,
                    search_profile_id=search_profile.id,
                    profile_label=search_profile.name or user_profile.display_name,
                )

        user_profile = session.execute(select(UserProfile).order_by(UserProfile.id.asc())).scalars().first()
        if user_profile is None:
            return None

        return LeadOwnerContext(
            user_profile_id=user_profile.id,
            search_profile_id=None,
            profile_label=user_profile.display_name,
        )

    def create_or_get_from_search_candidate(
        self,
        session: Session,
        *,
        candidate: SearchLeadCandidate,
        action: str,
        event_at: datetime | None = None,
        profile_id: int | None = None,
    ) -> LeadUpsertResult:
        owner_context = self.resolve_owner_context(session, profile_id=profile_id)
        if owner_context is None:
            raise ValueError("Сначала сохраните профиль пользователя, чтобы вести отклики.")

        lead = self._find_existing_lead(
            session,
            user_profile_id=owner_context.user_profile_id,
            candidate=candidate,
        )
        created = False
        duplicate = lead is not None
        effective_event_at = event_at or utc_now()

        if lead is None:
            lead, created = self._create_lead_from_candidate(
                session,
                user_profile_id=owner_context.user_profile_id,
                search_profile_id=owner_context.search_profile_id,
                candidate=candidate,
                event_at=effective_event_at,
            )
            duplicate = not created

        self._sync_candidate_snapshot(lead=lead, candidate=candidate)

        if action == "save":
            self.lead_event_service.record_event(
                session,
                lead,
                "saved",
                event_at=effective_event_at,
                payload={"vacancy_title": candidate.vacancy_title},
            )
        elif action == "viewed":
            self.lead_event_service.record_event(
                session,
                lead,
                "viewed",
                event_at=effective_event_at,
                payload={"vacancy_title": candidate.vacancy_title},
            )
        elif action == "opened_original_link":
            self.lead_event_service.record_event(
                session,
                lead,
                "viewed",
                event_at=effective_event_at,
                payload={"vacancy_title": candidate.vacancy_title},
            )
            self.lead_event_service.record_event(
                session,
                lead,
                "opened_original_link",
                event_at=effective_event_at,
                payload={
                    "vacancy_title": candidate.vacancy_title,
                    "original_url": candidate.original_url,
                },
                allow_repeat=True,
            )
        else:
            raise ValueError(f"Unsupported search lead action: {action}")

        session.flush()
        return LeadUpsertResult(lead=lead, created=created, duplicate=duplicate)

    def get_lead(self, session: Session, *, lead_id: int) -> ApplicationLead:
        lead = session.get(ApplicationLead, lead_id)
        if lead is None:
            raise LookupError("Лид не найден.")
        return lead

    def update_manual_details(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        application_channel: str | None,
        salary_expectation_text: str | None,
        contact_person: str | None,
        contact_email: str | None,
        resume_version_id: int | None = None,
        cover_letter_template_id: int | None = None,
        follow_up_due_at: datetime | None | object = UNSET,
        follow_up_sent_at: datetime | None | object = UNSET,
        next_action_at: datetime | None | object = UNSET,
    ) -> ApplicationLead:
        previous_follow_up_due_at = lead.follow_up_due_at
        lead.application_channel = application_channel
        lead.salary_expectation_text = salary_expectation_text
        lead.contact_person = contact_person
        lead.contact_email = contact_email
        if follow_up_due_at is not UNSET:
            lead.follow_up_due_at = follow_up_due_at
        if follow_up_sent_at is not UNSET:
            lead.follow_up_sent_at = follow_up_sent_at
        if next_action_at is not UNSET:
            lead.next_action_at = next_action_at
        elif follow_up_due_at is not UNSET and follow_up_due_at is not None and (
            lead.next_action_at is None or lead.next_action_at == previous_follow_up_due_at
        ):
            lead.next_action_at = follow_up_due_at
        lead.resume_version_id = resume_version_id
        lead.cover_letter_template_id = cover_letter_template_id
        session.flush()
        return lead

    def add_note(
        self,
        session: Session,
        *,
        lead: ApplicationLead,
        body: str,
        pinned: bool = False,
        event_at: datetime | None = None,
    ) -> ManualNote:
        normalized_body = body.strip()
        if not normalized_body:
            raise ValueError("Заметка не может быть пустой.")

        note = ManualNote(
            user_profile_id=lead.user_profile_id,
            search_profile_id=lead.search_profile_id,
            lead_id=lead.id,
            body=normalized_body,
            pinned=pinned,
        )
        session.add(note)
        session.flush()
        self.lead_event_service.record_event(
            session,
            lead,
            "note_added",
            event_at=event_at or utc_now(),
            payload={"note_id": note.id, "body": note.body, "pinned": note.pinned},
            allow_repeat=True,
        )
        return note

    def build_dashboard_data(self, session: Session) -> LeadDashboardData:
        try:
            owner_context = self.resolve_owner_context(session)
            if owner_context is None:
                return LeadDashboardData(
                    owner_context=None,
                    summary=LeadDashboardSummary(0, 0, 0, 0, 0),
                    leads=(),
                    reminders=(),
                    warning_message="Сначала сохраните профиль, чтобы вести отклики и напоминания.",
                )

            leads = tuple(
                session.execute(
                    select(ApplicationLead)
                    .where(ApplicationLead.user_profile_id == owner_context.user_profile_id)
                    .order_by(ApplicationLead.updated_at.desc(), ApplicationLead.id.desc())
                ).scalars()
            )
            reminder_rows = tuple(
                session.execute(
                    select(ReminderTask)
                    .where(
                        ReminderTask.user_profile_id == owner_context.user_profile_id,
                        ReminderTask.is_done.is_(False),
                    )
                    .order_by(ReminderTask.id.desc())
                ).scalars()
            )
            reminders = tuple(
                sorted(
                    reminder_rows,
                    key=lambda reminder: (
                        reminder.due_at is None,
                        reminder.due_at or datetime.max.replace(tzinfo=utc_now().tzinfo),
                        -reminder.id,
                    ),
                )
            )
            note_map = self._latest_note_by_lead(
                session,
                lead_ids=tuple(lead.id for lead in leads),
            )
            resume_label_map = self._resume_label_map(session)
            template_label_map = self._template_label_map(session)

            lead_cards = tuple(
                LeadCardView(
                    lead=lead,
                    status_label_ru=status_label(lead.status),
                    linked_resume_label=resume_label_map.get(lead.resume_version_id),
                    linked_template_name=template_label_map.get(lead.cover_letter_template_id),
                    latest_note=note_map.get(lead.id),
                    follow_up_due=_is_follow_up_due(lead),
                )
                for lead in self._order_leads(leads)
            )

            summary = LeadDashboardSummary(
                total_leads=len(leads),
                active_leads=sum(1 for lead in leads if lead.archived_at is None),
                applied_leads=sum(1 for lead in leads if lead.applied_at is not None),
                due_follow_ups=sum(1 for lead in leads if _is_follow_up_due(lead)),
                scheduled_interviews=sum(1 for lead in leads if lead.interview_at is not None and lead.archived_at is None),
            )

            return LeadDashboardData(
                owner_context=owner_context,
                summary=summary,
                leads=lead_cards,
                reminders=reminders[:5],
            )
        except SQLAlchemyError:
            return LeadDashboardData(
                owner_context=None,
                summary=LeadDashboardSummary(0, 0, 0, 0, 0),
                leads=(),
                reminders=(),
                warning_message="База CRM сейчас недоступна. Поиск продолжает работать, но отклики временно нельзя обновить.",
            )

    def build_detail_data(self, session: Session, *, lead_id: int) -> LeadDetailData:
        lead = self.get_lead(session, lead_id=lead_id)
        notes = tuple(
            session.execute(
                select(ManualNote)
                .where(ManualNote.lead_id == lead.id)
                .order_by(ManualNote.pinned.desc(), ManualNote.updated_at.desc(), ManualNote.id.desc())
            ).scalars()
        )
        reminder_rows = tuple(
            session.execute(
                select(ReminderTask)
                .where(ReminderTask.lead_id == lead.id)
                .order_by(ReminderTask.id.desc())
            ).scalars()
        )
        reminders = tuple(
            sorted(
                reminder_rows,
                key=lambda reminder: (
                    reminder.is_done,
                    reminder.due_at is None,
                    reminder.due_at or datetime.max.replace(tzinfo=utc_now().tzinfo),
                    -reminder.id,
                ),
            )
        )
        interviews = tuple(
            session.execute(
                select(InterviewEvent)
                .where(InterviewEvent.lead_id == lead.id)
                .order_by(InterviewEvent.interview_at.desc(), InterviewEvent.id.desc())
            ).scalars()
        )

        return LeadDetailData(
            lead=lead,
            status_label_ru=status_label(lead.status),
            notes=notes,
            reminders=reminders,
            interviews=interviews,
            timeline=self.lead_event_service.build_timeline(session, lead_id=lead.id),
            linked_resume_label=self._resume_label_map(session).get(lead.resume_version_id),
            linked_template_name=self._template_label_map(session).get(lead.cover_letter_template_id),
        )

    def _find_existing_lead(
        self,
        session: Session,
        *,
        user_profile_id: int,
        candidate: SearchLeadCandidate,
    ) -> ApplicationLead | None:
        if candidate.canonical_key:
            existing = session.execute(
                select(ApplicationLead)
                .where(
                    ApplicationLead.user_profile_id == user_profile_id,
                    ApplicationLead.canonical_key == candidate.canonical_key,
                )
                .order_by(ApplicationLead.id.asc())
            ).scalars().first()
            if existing is not None:
                return existing

        return session.execute(
            select(ApplicationLead)
            .where(
                ApplicationLead.user_profile_id == user_profile_id,
                ApplicationLead.source_id == candidate.source_id,
                ApplicationLead.source_external_id == candidate.source_external_id,
            )
            .order_by(ApplicationLead.id.asc())
        ).scalars().first()

    def _create_lead_from_candidate(
        self,
        session: Session,
        *,
        user_profile_id: int,
        search_profile_id: int | None,
        candidate: SearchLeadCandidate,
        event_at: datetime,
    ) -> tuple[ApplicationLead, bool]:
        try:
            with session.begin_nested():
                lead = ApplicationLead(
                    user_profile_id=user_profile_id,
                    search_profile_id=search_profile_id,
                    source_id=candidate.source_id,
                    source_name=candidate.source_name,
                    source_external_id=candidate.source_external_id,
                    canonical_key=candidate.canonical_key,
                    vacancy_title=candidate.vacancy_title,
                    translated_title_ru=candidate.translated_title_ru,
                    company_name=candidate.company_name,
                    location_text=candidate.location_text,
                    summary_ru=candidate.summary_ru,
                    original_url=candidate.original_url,
                    status="found",
                )
                session.add(lead)
                session.flush()
        except IntegrityError as exc:
            if not _is_source_identity_unique_conflict(exc):
                raise
            session.expire_all()
            with session.no_autoflush:
                existing_lead = self._find_existing_lead(
                    session,
                    user_profile_id=user_profile_id,
                    candidate=candidate,
                )
            if existing_lead is None:
                raise
            return existing_lead, False

        self.lead_event_service.record_event(
            session,
            lead,
            "found",
            event_at=event_at,
            payload=candidate.found_payload(),
        )
        return lead, True

    def _sync_candidate_snapshot(self, *, lead: ApplicationLead, candidate: SearchLeadCandidate) -> None:
        lead.source_id = candidate.source_id
        lead.source_name = candidate.source_name
        lead.source_external_id = candidate.source_external_id
        if _has_meaningful_text(candidate.canonical_key):
            lead.canonical_key = candidate.canonical_key.strip()
        lead.vacancy_title = candidate.vacancy_title
        lead.translated_title_ru = _merge_snapshot_text(lead.translated_title_ru, candidate.translated_title_ru)
        lead.company_name = _merge_snapshot_text(lead.company_name, candidate.company_name)
        lead.location_text = _merge_snapshot_text(lead.location_text, candidate.location_text)
        lead.summary_ru = _merge_snapshot_text(lead.summary_ru, candidate.summary_ru)
        lead.original_url = _merge_snapshot_text(lead.original_url, candidate.original_url)

    def _latest_note_by_lead(self, session: Session, *, lead_ids: tuple[int, ...]) -> dict[int, str]:
        if not lead_ids:
            return {}
        notes = session.execute(
            select(ManualNote)
            .where(ManualNote.lead_id.in_(lead_ids))
            .order_by(ManualNote.updated_at.desc(), ManualNote.id.desc())
        ).scalars()
        note_map: dict[int, str] = {}
        for note in notes:
            if note.lead_id is None or note.lead_id in note_map:
                continue
            note_map[note.lead_id] = note.body
        return note_map

    def _resume_label_map(self, session: Session) -> dict[int, str]:
        versions = session.execute(select(ResumeVersion).order_by(ResumeVersion.id.asc())).scalars()
        resumes = {
            resume.id: resume.title
            for resume in session.execute(select(Resume).order_by(Resume.id.asc())).scalars()
        }
        return {
            version.id: f"{resumes.get(version.resume_id, 'Резюме')} · {version.version_label}"
            for version in versions
        }

    def _template_label_map(self, session: Session) -> dict[int, str]:
        templates = session.execute(select(CoverLetterTemplate).order_by(CoverLetterTemplate.id.asc())).scalars()
        return {template.id: template.name for template in templates}

    def _order_leads(self, leads: tuple[ApplicationLead, ...]) -> tuple[ApplicationLead, ...]:
        far_future = datetime.max.replace(tzinfo=utc_now().tzinfo)
        return tuple(
            sorted(
                leads,
                key=lambda lead: (
                    lead.archived_at is not None,
                    lead.next_action_at is None,
                    lead.next_action_at or far_future,
                    -lead.updated_at.timestamp(),
                    -lead.id,
                ),
            )
        )


def _is_follow_up_due(lead: ApplicationLead) -> bool:
    if lead.follow_up_due_at is None:
        return False
    if lead.follow_up_sent_at is not None and lead.follow_up_sent_at >= lead.follow_up_due_at:
        return False
    return lead.follow_up_due_at <= utc_now()


def _merge_snapshot_text(current_value: str | None, incoming_value: str | None) -> str | None:
    cleaned_incoming = _clean_optional_text(incoming_value)
    if cleaned_incoming is None:
        return current_value
    return cleaned_incoming


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _has_meaningful_text(value: str | None) -> bool:
    return _clean_optional_text(value) is not None


def _is_source_identity_unique_conflict(error: IntegrityError) -> bool:
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if constraint_name == "uq_application_leads_user_source_identity":
        return True

    message = str(error.orig).lower()
    if "uq_application_leads_user_source_identity" in message:
        return True

    return "unique" in message and all(
        token in message
        for token in (
            "application_leads.user_profile_id",
            "application_leads.source_id",
            "application_leads.source_external_id",
        )
    )
