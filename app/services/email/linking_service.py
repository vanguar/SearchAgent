from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models.crm import ApplicationLead
from app.db.models.email import EmailMessage, EmailThread
from app.services.email.base import ManualLinkResult
from app.services.lead_event_service import LeadEventService


class EmailLinkingService:
    """Explicit manual link and unlink operations for threads and messages."""

    def __init__(self, *, lead_event_service: LeadEventService | None = None) -> None:
        self.lead_event_service = lead_event_service or LeadEventService()

    def link_thread(self, session: Session, *, thread_id: int, lead_id: int) -> ManualLinkResult:
        try:
            thread = session.get(EmailThread, thread_id)
            lead = session.get(ApplicationLead, lead_id)
            if thread is None or lead is None:
                return ManualLinkResult(
                    status="not_found",
                    notice_kind="warning",
                    notice_message_ru="Не удалось привязать цепочку: письмо или лид не найдены.",
                    commit_required=False,
                    redirect_thread_id=thread_id,
                    lead_id=lead_id,
                )
            if thread.lead_id == lead.id:
                return ManualLinkResult(
                    status="linked",
                    notice_kind="info",
                    notice_message_ru="Цепочка уже привязана к этому лиду.",
                    commit_required=False,
                    redirect_thread_id=thread.id,
                    lead_id=lead.id,
                )
            thread.lead_id = lead.id
            self.lead_event_service.record_event(
                session,
                lead,
                "email_thread_linked",
                payload={"thread_id": thread.id, "subject": thread.subject},
                allow_repeat=True,
            )
            session.flush()
            logger.info("email_thread_linked thread_id=%s lead_id=%s", thread.id, lead.id)
            return ManualLinkResult(
                status="linked",
                notice_kind="success",
                notice_message_ru="Цепочка вручную привязана к лиду.",
                commit_required=True,
                redirect_thread_id=thread.id,
                lead_id=lead.id,
            )
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("email_thread_link_skipped reason=db_unavailable error=%s", exc.__class__.__name__)
            return ManualLinkResult(
                status="db_unavailable",
                notice_kind="warning",
                notice_message_ru="База CRM временно недоступна. Привязка цепочки не сохранена.",
                commit_required=False,
                redirect_thread_id=thread_id,
                lead_id=lead_id,
            )

    def unlink_thread(self, session: Session, *, thread_id: int) -> ManualLinkResult:
        try:
            thread = session.get(EmailThread, thread_id)
            if thread is None:
                return ManualLinkResult(
                    status="not_found",
                    notice_kind="warning",
                    notice_message_ru="Не удалось отвязать цепочку: запись не найдена.",
                    commit_required=False,
                    redirect_thread_id=thread_id,
                )
            if thread.lead_id is None:
                return ManualLinkResult(
                    status="unlinked",
                    notice_kind="info",
                    notice_message_ru="Цепочка уже не привязана к лиду.",
                    commit_required=False,
                    redirect_thread_id=thread.id,
                )
            previous_lead = session.get(ApplicationLead, thread.lead_id) if thread.lead_id else None
            thread.lead_id = None
            if previous_lead is not None:
                self.lead_event_service.record_event(
                    session,
                    previous_lead,
                    "email_thread_unlinked",
                    payload={"thread_id": thread.id, "subject": thread.subject},
                    allow_repeat=True,
                )
            session.flush()
            logger.info("email_thread_unlinked thread_id=%s", thread.id)
            return ManualLinkResult(
                status="unlinked",
                notice_kind="success",
                notice_message_ru="Цепочка отвязана от лида.",
                commit_required=True,
                redirect_thread_id=thread.id,
            )
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("email_thread_unlink_skipped reason=db_unavailable error=%s", exc.__class__.__name__)
            return ManualLinkResult(
                status="db_unavailable",
                notice_kind="warning",
                notice_message_ru="База CRM временно недоступна. Отвязка цепочки не сохранена.",
                commit_required=False,
                redirect_thread_id=thread_id,
            )

    def link_message(self, session: Session, *, message_id: int, lead_id: int) -> ManualLinkResult:
        try:
            message = session.get(EmailMessage, message_id)
            lead = session.get(ApplicationLead, lead_id)
            if message is None or lead is None:
                return ManualLinkResult(
                    status="not_found",
                    notice_kind="warning",
                    notice_message_ru="Не удалось привязать сообщение: письмо или лид не найдены.",
                    commit_required=False,
                    message_id=message_id,
                    lead_id=lead_id,
                )
            if message.lead_id == lead.id:
                return ManualLinkResult(
                    status="linked",
                    notice_kind="info",
                    notice_message_ru="Сообщение уже привязано к этому лиду.",
                    commit_required=False,
                    redirect_thread_id=message.thread_id,
                    message_id=message.id,
                    lead_id=lead.id,
                )
            message.lead_id = lead.id
            self.lead_event_service.record_event(
                session,
                lead,
                "email_message_linked",
                payload={"message_id": message.id, "thread_id": message.thread_id, "subject": message.subject},
                allow_repeat=True,
            )
            session.flush()
            logger.info("email_message_linked message_id=%s lead_id=%s", message.id, lead.id)
            return ManualLinkResult(
                status="linked",
                notice_kind="success",
                notice_message_ru="Сообщение вручную привязано к лиду.",
                commit_required=True,
                redirect_thread_id=message.thread_id,
                message_id=message.id,
                lead_id=lead.id,
            )
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("email_message_link_skipped reason=db_unavailable error=%s", exc.__class__.__name__)
            return ManualLinkResult(
                status="db_unavailable",
                notice_kind="warning",
                notice_message_ru="База CRM временно недоступна. Привязка сообщения не сохранена.",
                commit_required=False,
                message_id=message_id,
                lead_id=lead_id,
            )

    def unlink_message(self, session: Session, *, message_id: int) -> ManualLinkResult:
        try:
            message = session.get(EmailMessage, message_id)
            if message is None:
                return ManualLinkResult(
                    status="not_found",
                    notice_kind="warning",
                    notice_message_ru="Не удалось отвязать сообщение: запись не найдена.",
                    commit_required=False,
                    message_id=message_id,
                )
            if message.lead_id is None:
                return ManualLinkResult(
                    status="unlinked",
                    notice_kind="info",
                    notice_message_ru="Сообщение уже не привязано к лиду.",
                    commit_required=False,
                    redirect_thread_id=message.thread_id,
                    message_id=message.id,
                )
            previous_lead = session.get(ApplicationLead, message.lead_id) if message.lead_id else None
            message.lead_id = None
            if previous_lead is not None:
                self.lead_event_service.record_event(
                    session,
                    previous_lead,
                    "email_message_unlinked",
                    payload={"message_id": message.id, "thread_id": message.thread_id, "subject": message.subject},
                    allow_repeat=True,
                )
            session.flush()
            logger.info("email_message_unlinked message_id=%s", message.id)
            return ManualLinkResult(
                status="unlinked",
                notice_kind="success",
                notice_message_ru="Сообщение отвязано от лида.",
                commit_required=True,
                redirect_thread_id=message.thread_id,
                message_id=message.id,
            )
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("email_message_unlink_skipped reason=db_unavailable error=%s", exc.__class__.__name__)
            return ManualLinkResult(
                status="db_unavailable",
                notice_kind="warning",
                notice_message_ru="База CRM временно недоступна. Отвязка сообщения не сохранена.",
                commit_required=False,
                message_id=message_id,
            )
