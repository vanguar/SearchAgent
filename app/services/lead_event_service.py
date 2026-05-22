from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.models.crm import ApplicationLead, LeadEvent

LeadEventType = str

EVENT_LABELS: dict[LeadEventType, str] = {
    "found": "Вакансия найдена",
    "viewed": "Просмотрено",
    "opened_original_link": "Открыт оригинал",
    "saved": "Сохранено в отклики",
    "applied": "Отклик отправлен",
    "reply_received": "Получен ответ",
    "rejected": "Получен отказ",
    "interview_scheduled": "Назначено собеседование",
    "archived": "Лид архивирован",
    "note_added": "Добавлена заметка",
    "reminder_added": "Добавлено напоминание",
    "email_thread_linked": "Привязана цепочка писем",
    "email_thread_unlinked": "Отвязана цепочка писем",
    "email_message_linked": "Привязано письмо",
    "email_message_unlinked": "Отвязано письмо",
}

STATUS_LABELS: dict[str, str] = {
    "found": "Найдено",
    "viewed": "Просмотрено",
    "saved": "Сохранено",
    "applied": "Отклик отправлен",
    "reply_received": "Есть ответ",
    "interview_scheduled": "Собеседование",
    "rejected": "Отказ",
    "archived": "В архиве",
}


@dataclass(frozen=True, slots=True)
class LeadEventResult:
    event: LeadEvent | None
    created: bool


@dataclass(frozen=True, slots=True)
class LeadTimelineEntry:
    event_id: int
    event_type: str
    label_ru: str
    occurred_at: datetime
    details_ru: str | None
    payload: dict[str, Any]


def status_label(status: str | None) -> str:
    if not status:
        return "Новый лид"
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize())


class LeadEventService:
    """Explicit event tracking with deterministic field updates."""

    def record_event(
        self,
        session: Session,
        lead: ApplicationLead,
        event_type: LeadEventType,
        *,
        event_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
        allow_repeat: bool = False,
    ) -> LeadEventResult:
        if event_type not in EVENT_LABELS:
            raise ValueError(f"Unsupported lead event: {event_type}")

        if not allow_repeat and self._should_skip(lead=lead, event_type=event_type):
            self._apply_state(lead=lead, event_type=event_type, event_at=event_at or utc_now())
            return LeadEventResult(event=None, created=False)

        normalized_payload = _normalize_payload(payload)
        occurred_at = event_at or utc_now()
        self._apply_state(lead=lead, event_type=event_type, event_at=occurred_at)

        event = LeadEvent(
            lead_id=lead.id,
            event_type=event_type,
            event_at=occurred_at,
            payload=normalized_payload or None,
        )
        session.add(event)
        session.flush()
        return LeadEventResult(event=event, created=True)

    def build_timeline(self, session: Session, *, lead_id: int) -> tuple[LeadTimelineEntry, ...]:
        events = session.execute(
            select(LeadEvent)
            .where(LeadEvent.lead_id == lead_id)
            .order_by(LeadEvent.event_at.desc(), LeadEvent.id.desc())
        ).scalars()
        return tuple(
            LeadTimelineEntry(
                event_id=event.id,
                event_type=event.event_type,
                label_ru=EVENT_LABELS.get(event.event_type, event.event_type),
                occurred_at=event.event_at,
                details_ru=self._build_details(event.event_type, event.payload or {}),
                payload=event.payload or {},
            )
            for event in events
        )

    def _should_skip(self, *, lead: ApplicationLead, event_type: LeadEventType) -> bool:
        if event_type == "found":
            return lead.found_at is not None
        if event_type == "viewed":
            return lead.viewed_at is not None
        if event_type == "saved":
            return lead.saved_at is not None
        return False

    def _apply_state(self, *, lead: ApplicationLead, event_type: LeadEventType, event_at: datetime) -> None:
        if event_type == "found" and lead.found_at is None:
            lead.found_at = event_at
            if not lead.status:
                lead.status = "found"
            return

        if event_type == "viewed":
            if lead.viewed_at is None:
                lead.viewed_at = event_at
            if lead.status in {None, "found"}:
                lead.status = "viewed"
            return

        if event_type == "opened_original_link":
            lead.opened_original_at = event_at
            if lead.viewed_at is None:
                lead.viewed_at = event_at
            if lead.status in {None, "found"}:
                lead.status = "viewed"
            return

        if event_type == "saved":
            if lead.saved_at is None:
                lead.saved_at = event_at
            if lead.status in {None, "found", "viewed"}:
                lead.status = "saved"
            return

        if event_type == "applied":
            if lead.applied_at is None:
                lead.applied_at = event_at
            lead.status = "applied"
            return

        if event_type == "reply_received":
            if lead.reply_at is None:
                lead.reply_at = event_at
            lead.status = "reply_received"
            return

        if event_type == "rejected":
            if lead.rejected_at is None:
                lead.rejected_at = event_at
            lead.status = "rejected"
            return

        if event_type == "interview_scheduled":
            if lead.interview_at is None:
                lead.interview_at = event_at
            lead.status = "interview_scheduled"
            return

        if event_type == "archived":
            if lead.archived_at is None:
                lead.archived_at = event_at
            lead.status = "archived"

    def _build_details(self, event_type: LeadEventType, payload: dict[str, Any]) -> str | None:
        details: list[str] = []

        if event_type == "found":
            if payload.get("vacancy_title"):
                details.append(str(payload["vacancy_title"]))
            if payload.get("source_name"):
                details.append(f"Источник: {payload['source_name']}")

        if event_type == "opened_original_link" and payload.get("original_url"):
            details.append(str(payload["original_url"]))

        if event_type == "applied":
            if payload.get("application_channel"):
                details.append(f"Канал: {payload['application_channel']}")
            if payload.get("salary_expectation_text"):
                details.append(f"Ожидание: {payload['salary_expectation_text']}")
            if payload.get("contact_person"):
                details.append(f"Контакт: {payload['contact_person']}")
            if payload.get("contact_email"):
                details.append(str(payload["contact_email"]))
            if payload.get("follow_up_due_at"):
                details.append(f"Фоллоу-ап: {payload['follow_up_due_at']}")

        if event_type == "reply_received" and payload.get("details"):
            details.append(str(payload["details"]))

        if event_type == "rejected" and payload.get("reason"):
            details.append(str(payload["reason"]))

        if event_type == "interview_scheduled":
            if payload.get("interview_format"):
                details.append(f"Формат: {payload['interview_format']}")
            if payload.get("interviewer_name"):
                details.append(f"Контакт: {payload['interviewer_name']}")
            if payload.get("location"):
                details.append(f"Локация: {payload['location']}")
            if payload.get("notes"):
                details.append(str(payload["notes"]))

        if event_type == "note_added" and payload.get("body"):
            details.append(str(payload["body"]))

        if event_type == "reminder_added":
            if payload.get("title"):
                details.append(str(payload["title"]))
            if payload.get("due_at"):
                details.append(f"Срок: {payload['due_at']}")

        if event_type in {"email_thread_linked", "email_thread_unlinked"}:
            if payload.get("subject"):
                details.append(str(payload["subject"]))
            if payload.get("thread_id"):
                details.append(f"Цепочка #{payload['thread_id']}")

        if event_type in {"email_message_linked", "email_message_unlinked"}:
            if payload.get("subject"):
                details.append(str(payload["subject"]))
            if payload.get("message_id"):
                details.append(f"Письмо #{payload['message_id']}")

        if not details:
            return None
        return " · ".join(details)


def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, datetime):
            normalized[key] = value.isoformat()
        else:
            normalized[key] = value
    return normalized
