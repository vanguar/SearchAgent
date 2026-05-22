from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models.crm import ApplicationLead
from app.db.models.email import EmailMessage, EmailThread
from app.services.email.base import EmailValidationError, LeadMatchSnapshot, ManualEmailIngestResult, ManualEmailInput
from app.services.email.email_parser import EmailParser, clean_optional_text
from app.services.email.thread_matcher import ThreadMatcher
from app.services.lead_event_service import LeadEventService
from app.services.lead_service import LeadService


class ManualEmailIngestService:
    """Manual-first email import with deterministic parsing and matching."""

    def __init__(
        self,
        *,
        parser: EmailParser | None = None,
        matcher: ThreadMatcher | None = None,
        lead_service: LeadService | None = None,
        lead_event_service: LeadEventService | None = None,
    ) -> None:
        self.parser = parser or EmailParser()
        self.matcher = matcher or ThreadMatcher()
        self.lead_service = lead_service or LeadService()
        self.lead_event_service = lead_event_service or LeadEventService()

    def build_input(self, form_data: Mapping[str, object]) -> ManualEmailInput:
        import_mode = str(form_data.get("import_mode", "message")).strip() or "message"
        if import_mode not in {"message", "thread_only"}:
            import_mode = "message"
        direction_mode = str(form_data.get("direction_mode", "auto")).strip() or "auto"
        if direction_mode not in {"auto", "incoming", "outgoing"}:
            direction_mode = "auto"
        lead_id = _parse_optional_int(
            form_data.get("lead_id"),
            field_label_ru="лида",
            message_ru="Некорректный ID лида. Выберите лид из списка и попробуйте снова.",
        )
        return ManualEmailInput(
            import_mode=import_mode,
            subject=_string_value(form_data.get("subject")),
            participant_emails_raw=_string_value(form_data.get("participant_emails")),
            from_email_raw=_string_value(form_data.get("from_email")),
            to_emails_raw=_string_value(form_data.get("to_emails")),
            cc_emails_raw=_string_value(form_data.get("cc_emails")),
            bcc_emails_raw=_string_value(form_data.get("bcc_emails")),
            body_text=_string_value(form_data.get("body_text")),
            sent_at_text=_string_value(form_data.get("sent_at")),
            external_message_id=_string_value(form_data.get("external_message_id")),
            external_thread_id=_string_value(form_data.get("external_thread_id")),
            lead_id=lead_id,
            direction_mode=direction_mode,
        )

    def ingest(
        self,
        session: Session,
        *,
        payload: ManualEmailInput,
        known_user_emails: tuple[str, ...],
    ) -> ManualEmailIngestResult:
        try:
            owner_context = self.lead_service.resolve_owner_context(session)
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("manual_email_import_skipped reason=db_unavailable error=%s", exc.__class__.__name__)
            return _db_unavailable_result()

        if owner_context is None:
            logger.info("manual_email_import_skipped reason=owner_missing")
            return ManualEmailIngestResult(
                status="owner_missing",
                notice_kind="warning",
                notice_message_ru="Сначала сохраните профиль, чтобы импортировать письма и привязывать их к лидам.",
                commit_required=False,
            )

        try:
            parsed_payload = self.parser.parse_manual_input(payload, known_user_emails=known_user_emails)
            if payload.import_mode == "thread_only":
                if not parsed_payload.subject_clean and not payload.external_thread_id:
                    return _validation_result("Для импорта только цепочки укажите тему или внешний ID цепочки.")
            elif not (parsed_payload.subject_clean or parsed_payload.body_text or parsed_payload.from_email):
                return _validation_result("Для импорта сообщения укажите тему, отправителя или текст письма.")

            explicit_lead = None
            if payload.lead_id is not None:
                explicit_lead = session.get(ApplicationLead, payload.lead_id)
                if explicit_lead is None or explicit_lead.user_profile_id != owner_context.user_profile_id:
                    return _validation_result("Выбранный лид не найден в текущем профиле.")

            thread = session.execute(
                select(EmailThread)
                .where(
                    EmailThread.user_profile_id == owner_context.user_profile_id,
                    EmailThread.external_thread_id == parsed_payload.external_thread_id,
                )
                .order_by(EmailThread.id.asc())
            ).scalars().first()
            if thread is None:
                thread = EmailThread(
                    user_profile_id=owner_context.user_profile_id,
                    external_thread_id=parsed_payload.external_thread_id,
                )
                session.add(thread)

            thread_lead_changed = False
            thread.subject = parsed_payload.subject_clean or thread.subject or "Без темы"
            thread.participant_emails = list(parsed_payload.participant_emails) or thread.participant_emails
            if parsed_payload.sent_at is not None:
                if thread.last_message_at is None or parsed_payload.sent_at > thread.last_message_at:
                    thread.last_message_at = parsed_payload.sent_at
            if explicit_lead is not None:
                thread_lead_changed = thread.lead_id != explicit_lead.id
                thread.lead_id = explicit_lead.id

            message = None
            message_lead_changed = False
            if payload.import_mode == "message":
                session.flush()
                message = session.execute(
                    select(EmailMessage)
                    .where(
                        EmailMessage.thread_id == thread.id,
                        EmailMessage.external_message_id == parsed_payload.external_message_id,
                    )
                    .order_by(EmailMessage.id.asc())
                ).scalars().first()
                if message is None:
                    message = EmailMessage(
                        thread_id=thread.id,
                        external_message_id=parsed_payload.external_message_id,
                    )
                    session.add(message)
                message.subject = parsed_payload.subject_clean or "Без темы"
                message.from_email = parsed_payload.from_email
                message.to_emails = list(parsed_payload.to_emails)
                message.cc_emails = list(parsed_payload.cc_emails)
                message.bcc_emails = list(parsed_payload.bcc_emails)
                message.sent_at = parsed_payload.sent_at
                message.received_at = parsed_payload.sent_at
                message.body_text = parsed_payload.body_text
                message.is_incoming = parsed_payload.direction != "outgoing"
                if explicit_lead is not None:
                    message_lead_changed = message.lead_id != explicit_lead.id
                    message.lead_id = explicit_lead.id
                message.raw_payload = {
                    "import_source": "manual",
                    "parsed": {
                        "subject_clean": parsed_payload.subject_clean,
                        "participant_emails": list(parsed_payload.participant_emails),
                        "contact_emails": list(parsed_payload.contact_emails),
                        "extracted_urls": list(parsed_payload.extracted_urls),
                        "thread_token": parsed_payload.thread_token,
                        "direction": parsed_payload.direction,
                        "direction_source": parsed_payload.direction_source,
                        "cues": {
                            "reply": parsed_payload.cues.has_reply_signal,
                            "rejection": parsed_payload.cues.has_rejection_signal,
                            "interview": parsed_payload.cues.has_interview_signal,
                        },
                    },
                }

            session.flush()

            lead_snapshots = _load_lead_snapshots(session, user_profile_id=owner_context.user_profile_id)
            match_result = self.matcher.match(
                parsed_payload=parsed_payload,
                leads=lead_snapshots,
                explicit_lead_id=payload.lead_id,
            )
            logger.info(
                "email_match_result status=%s thread_id=%s message_id=%s",
                match_result.status,
                thread.id,
                message.id if message is not None else None,
            )

            if explicit_lead is not None:
                if thread_lead_changed:
                    self.lead_event_service.record_event(
                        session,
                        explicit_lead,
                        "email_thread_linked",
                        payload={"thread_id": thread.id, "subject": thread.subject},
                        allow_repeat=True,
                    )
                if message is not None and message_lead_changed:
                    self.lead_event_service.record_event(
                        session,
                        explicit_lead,
                        "email_message_linked",
                        payload={"message_id": message.id, "thread_id": thread.id, "subject": message.subject},
                        allow_repeat=True,
                    )

            session.flush()
            logger.info(
                "manual_email_import_success thread_id=%s message_id=%s linked_lead_id=%s",
                thread.id,
                message.id if message is not None else None,
                explicit_lead.id if explicit_lead is not None else None,
            )
            return ManualEmailIngestResult(
                status="imported",
                notice_kind="success",
                notice_message_ru=(
                    "Письмо импортировано и привязано к лиду."
                    if explicit_lead is not None
                    else "Письмо импортировано. Проверьте кандидатов на привязку справа."
                ),
                commit_required=True,
                redirect_thread_id=thread.id,
                message_id=message.id if message is not None else None,
                linked_lead_id=explicit_lead.id if explicit_lead is not None else None,
                match_result=match_result,
            )
        except EmailValidationError as exc:
            return _validation_result(exc.message_ru)
        except SQLAlchemyError as exc:
            session.rollback()
            logger.warning("manual_email_import_skipped reason=db_unavailable error=%s", exc.__class__.__name__)
            return _db_unavailable_result()


def _load_lead_snapshots(session: Session, *, user_profile_id: int) -> tuple[LeadMatchSnapshot, ...]:
    leads = session.execute(
        select(ApplicationLead)
        .where(ApplicationLead.user_profile_id == user_profile_id)
        .order_by(ApplicationLead.id.asc())
    ).scalars()
    return tuple(
        LeadMatchSnapshot(
            id=lead.id,
            vacancy_title=lead.vacancy_title,
            translated_title_ru=lead.translated_title_ru,
            company_name=lead.company_name,
            contact_email=lead.contact_email,
            original_url=lead.original_url,
            source_external_id=lead.source_external_id,
            status=lead.status,
        )
        for lead in leads
    )


def _string_value(value: object) -> str | None:
    return clean_optional_text(str(value)) if value is not None else None


def _parse_optional_int(value: object, *, field_label_ru: str, message_ru: str | None = None) -> int | None:
    cleaned = _string_value(value)
    if cleaned is None:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise EmailValidationError(message_ru or f"Некорректный ID {field_label_ru}.") from exc
    if parsed <= 0:
        raise EmailValidationError(message_ru or f"Некорректный ID {field_label_ru}.")
    return parsed


def _validation_result(message: str) -> ManualEmailIngestResult:
    logger.info("manual_email_import_validation_failed reason=%s", message)
    return ManualEmailIngestResult(
        status="validation_error",
        notice_kind="warning",
        notice_message_ru=message,
        commit_required=False,
    )


def _db_unavailable_result() -> ManualEmailIngestResult:
    return ManualEmailIngestResult(
        status="db_unavailable",
        notice_kind="warning",
        notice_message_ru="База CRM временно недоступна. Ручной импорт письма не сохранен.",
        commit_required=False,
    )
