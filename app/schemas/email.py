from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class EmailThreadSchema(ORMBaseSchema):
    id: int
    user_profile_id: int
    lead_id: int | None = None
    external_thread_id: str
    subject: str | None = None
    participant_emails: list[str] | None = None
    last_message_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EmailMessageSchema(ORMBaseSchema):
    id: int
    thread_id: int
    lead_id: int | None = None
    external_message_id: str
    from_email: str | None = None
    to_emails: list[str] | None = None
    cc_emails: list[str] | None = None
    bcc_emails: list[str] | None = None
    sent_at: datetime | None = None
    received_at: datetime | None = None
    reply_at: datetime | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_incoming: bool
    raw_payload: dict | None = None
    created_at: datetime
