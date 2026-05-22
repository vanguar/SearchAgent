from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models.crm import ApplicationLead
from app.db.models.email import EmailMessage, EmailThread
from app.services.email.base import (
    EmailLeadOption,
    EmailMessageView,
    EmailNotice,
    EmailThreadListItem,
    EmailWorkspaceData,
    LeadEmailSummary,
    LeadEmailThreadSummary,
    LeadMatchSnapshot,
    SelectedThreadView,
)
from app.services.email.email_parser import EmailParser
from app.services.email.gmail_client import GmailClient
from app.services.email.thread_matcher import ThreadMatcher
from app.services.lead_event_service import status_label
from app.services.lead_service import LeadService


class EmailWorkspaceService:
    """Page-oriented read model for the Email workspace and lead detail email summaries."""

    def __init__(
        self,
        *,
        parser: EmailParser | None = None,
        matcher: ThreadMatcher | None = None,
        gmail_client: GmailClient | None = None,
        lead_service: LeadService | None = None,
    ) -> None:
        self.parser = parser or EmailParser()
        self.matcher = matcher or ThreadMatcher()
        self.gmail_client = gmail_client or GmailClient()
        self.lead_service = lead_service or LeadService()

    def build_workspace(
        self,
        session: Session,
        *,
        selected_thread_id: int | None,
        notice: EmailNotice | None,
        profile_id: int | None = None,
    ) -> EmailWorkspaceData:
        gmail_state = self.gmail_client.configuration_state()
        try:
            owner_context = self.lead_service.resolve_owner_context(session, profile_id=profile_id)
            if owner_context is None:
                return EmailWorkspaceData(
                    owner_label=None,
                    warning_message="Сначала сохраните профиль пользователя, чтобы вести переписку и привязки к лидам.",
                    notice=notice,
                    degraded=False,
                    can_import=False,
                    threads=(),
                    selected_thread=None,
                    lead_options=(),
                    gmail_state=gmail_state,
                )

            leads = tuple(
                session.execute(
                    select(ApplicationLead)
                    .where(ApplicationLead.user_profile_id == owner_context.user_profile_id)
                    .order_by(ApplicationLead.updated_at.desc(), ApplicationLead.id.desc())
                ).scalars()
            )
            lead_lookup = {lead.id: lead for lead in leads}
            lead_options = tuple(
                EmailLeadOption(
                    lead_id=lead.id,
                    label_ru=f"{lead.translated_title_ru or lead.vacancy_title} · {lead.company_name or 'Компания не указана'} · {status_label(lead.status)}",
                )
                for lead in leads
            )

            threads = tuple(
                session.execute(
                    select(EmailThread)
                    .where(EmailThread.user_profile_id == owner_context.user_profile_id)
                    .order_by(EmailThread.last_message_at.desc(), EmailThread.id.desc())
                ).scalars()
            )
            messages = tuple(
                session.execute(
                    select(EmailMessage)
                    .join(EmailThread, EmailThread.id == EmailMessage.thread_id)
                    .where(EmailThread.user_profile_id == owner_context.user_profile_id)
                    .order_by(EmailMessage.sent_at.desc(), EmailMessage.created_at.desc(), EmailMessage.id.desc())
                ).scalars()
            )
            messages_by_thread: dict[int, list[EmailMessage]] = defaultdict(list)
            for message in messages:
                messages_by_thread[message.thread_id].append(message)

            chosen_thread = None
            if selected_thread_id is not None:
                chosen_thread = next((thread for thread in threads if thread.id == selected_thread_id), None)
            if chosen_thread is None and threads:
                chosen_thread = threads[0]

            thread_items = tuple(
                self._build_thread_item(
                    thread=thread,
                    messages=tuple(messages_by_thread.get(thread.id, ())),
                    lead_lookup=lead_lookup,
                    selected=chosen_thread is not None and thread.id == chosen_thread.id,
                )
                for thread in threads
            )

            selected_thread = None
            if chosen_thread is not None:
                selected_messages = tuple(messages_by_thread.get(chosen_thread.id, ()))
                message_views = tuple(
                    self._build_message_view(
                        thread=chosen_thread,
                        message=message,
                        lead_lookup=lead_lookup,
                    )
                    for message in selected_messages
                )
                parsed_payload = self._build_thread_payload(thread=chosen_thread, messages=selected_messages)
                match_result = self.matcher.match(
                    parsed_payload=parsed_payload,
                    leads=tuple(_lead_snapshot(lead) for lead in leads),
                )
                selected_thread = SelectedThreadView(
                    thread_id=chosen_thread.id,
                    subject=chosen_thread.subject or "Без темы",
                    participant_line=_participant_line(chosen_thread.participant_emails),
                    linked_lead_id=chosen_thread.lead_id,
                    linked_lead_label=_lead_label(lead_lookup.get(chosen_thread.lead_id)),
                    messages=message_views,
                    match_result=match_result,
                )

            return EmailWorkspaceData(
                owner_label=owner_context.profile_label,
                warning_message=None,
                notice=notice,
                degraded=False,
                can_import=True,
                threads=thread_items,
                selected_thread=selected_thread,
                lead_options=lead_options,
                gmail_state=gmail_state,
            )
        except SQLAlchemyError as exc:
            logger.warning("email_workspace_db_unavailable error=%s", exc.__class__.__name__)
            return EmailWorkspaceData(
                owner_label=None,
                warning_message="База CRM сейчас недоступна. Email workspace открыт в деградированном режиме.",
                notice=notice,
                degraded=True,
                can_import=False,
                threads=(),
                selected_thread=None,
                lead_options=(),
                gmail_state=gmail_state,
            )

    def build_lead_email_summary(self, session: Session, *, lead_id: int) -> LeadEmailSummary:
        try:
            direct_threads = tuple(
                session.execute(
                    select(EmailThread)
                    .where(EmailThread.lead_id == lead_id)
                    .order_by(EmailThread.last_message_at.desc(), EmailThread.id.desc())
                ).scalars()
            )
            direct_messages = tuple(
                session.execute(
                    select(EmailMessage)
                    .where(EmailMessage.lead_id == lead_id)
                    .order_by(EmailMessage.sent_at.desc(), EmailMessage.id.desc())
                ).scalars()
            )
            thread_ids = {thread.id for thread in direct_threads} | {message.thread_id for message in direct_messages}
            if not thread_ids:
                return LeadEmailSummary(warning_message=None, threads=())

            threads = tuple(
                session.execute(
                    select(EmailThread)
                    .where(EmailThread.id.in_(thread_ids))
                    .order_by(EmailThread.last_message_at.desc(), EmailThread.id.desc())
                ).scalars()
            )
            messages = tuple(
                session.execute(
                    select(EmailMessage)
                    .where(EmailMessage.thread_id.in_(thread_ids))
                    .order_by(EmailMessage.sent_at.desc(), EmailMessage.id.desc())
                ).scalars()
            )
            counts: dict[int, int] = defaultdict(int)
            for message in messages:
                counts[message.thread_id] += 1
            return LeadEmailSummary(
                warning_message=None,
                threads=tuple(
                    LeadEmailThreadSummary(
                        thread_id=thread.id,
                        subject=thread.subject or "Без темы",
                        participant_line=_participant_line(thread.participant_emails),
                        message_count=counts.get(thread.id, 0),
                        last_message_at=thread.last_message_at,
                    )
                    for thread in threads
                ),
            )
        except SQLAlchemyError as exc:
            logger.warning("lead_email_summary_unavailable lead_id=%s error=%s", lead_id, exc.__class__.__name__)
            return LeadEmailSummary(
                warning_message="Связанную переписку пока не удалось загрузить: база CRM временно недоступна.",
                threads=(),
            )

    def _build_thread_item(
        self,
        *,
        thread: EmailThread,
        messages: tuple[EmailMessage, ...],
        lead_lookup: dict[int, ApplicationLead],
        selected: bool,
    ) -> EmailThreadListItem:
        parsed_payload = self._build_thread_payload(thread=thread, messages=messages)
        return EmailThreadListItem(
            thread_id=thread.id,
            subject=thread.subject or "Без темы",
            participant_line=_participant_line(thread.participant_emails),
            message_count=len(messages),
            last_message_at=thread.last_message_at,
            linked_lead_id=thread.lead_id,
            linked_lead_label=_lead_label(lead_lookup.get(thread.lead_id)),
            cue_labels_ru=parsed_payload.cues.labels_ru,
            selected=selected,
        )

    def _build_message_view(
        self,
        *,
        thread: EmailThread,
        message: EmailMessage,
        lead_lookup: dict[int, ApplicationLead],
    ) -> EmailMessageView:
        parsed_payload = self.parser.parse_message_snapshot(
            subject=message.subject,
            from_email=message.from_email,
            to_emails=tuple(message.to_emails or ()),
            cc_emails=tuple(message.cc_emails or ()),
            bcc_emails=tuple(message.bcc_emails or ()),
            participant_emails=tuple(thread.participant_emails or ()),
            body_text=message.body_text,
            sent_at=message.sent_at or message.received_at,
            external_message_id=message.external_message_id,
            external_thread_id=thread.external_thread_id,
            direction_mode="incoming" if message.is_incoming else "outgoing",
        )
        return EmailMessageView(
            message_id=message.id,
            subject=message.subject or "Без темы",
            from_email=message.from_email,
            recipients_line=", ".join(message.to_emails or ()) or "Получатели не указаны",
            sent_at=message.sent_at or message.received_at,
            direction_label_ru=_direction_label(parsed_payload.direction),
            cue_labels_ru=parsed_payload.cues.labels_ru,
            extracted_contact_emails=parsed_payload.contact_emails,
            extracted_urls=parsed_payload.extracted_urls,
            body_preview=_body_preview(message.body_text),
            linked_lead_id=message.lead_id,
            linked_lead_label=_lead_label(lead_lookup.get(message.lead_id)),
        )

    def _build_thread_payload(self, *, thread: EmailThread, messages: tuple[EmailMessage, ...]):
        latest_message = messages[0] if messages else None
        return self.parser.parse_message_snapshot(
            subject=(latest_message.subject if latest_message else thread.subject),
            from_email=latest_message.from_email if latest_message else None,
            to_emails=tuple(latest_message.to_emails or ()) if latest_message else (),
            cc_emails=tuple(latest_message.cc_emails or ()) if latest_message else (),
            bcc_emails=tuple(latest_message.bcc_emails or ()) if latest_message else (),
            participant_emails=tuple(thread.participant_emails or ()),
            body_text=latest_message.body_text if latest_message else None,
            sent_at=latest_message.sent_at if latest_message else thread.last_message_at,
            external_message_id=latest_message.external_message_id if latest_message else None,
            external_thread_id=thread.external_thread_id,
        )


def _lead_snapshot(lead: ApplicationLead) -> LeadMatchSnapshot:
    return LeadMatchSnapshot(
        id=lead.id,
        vacancy_title=lead.vacancy_title,
        translated_title_ru=lead.translated_title_ru,
        company_name=lead.company_name,
        contact_email=lead.contact_email,
        original_url=lead.original_url,
        source_external_id=lead.source_external_id,
        status=lead.status,
    )


def _lead_label(lead: ApplicationLead | None) -> str | None:
    if lead is None:
        return None
    status = status_label(lead.status)
    company = lead.company_name or "Компания не указана"
    return f"{lead.translated_title_ru or lead.vacancy_title} · {company} · {status}"


def _participant_line(participants: list[str] | None) -> str:
    if not participants:
        return "Участники не указаны"
    return ", ".join(participants)


def _direction_label(direction: str) -> str:
    if direction == "incoming":
        return "Входящее"
    if direction == "outgoing":
        return "Исходящее"
    return "Направление не определено"


def _body_preview(body_text: str | None) -> str:
    if not body_text:
        return "Текст письма не сохранен."
    collapsed = " ".join(body_text.split())
    if len(collapsed) <= 240:
        return collapsed
    return f"{collapsed[:237]}..."
