from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TimestampMixin


class EmailThread(TimestampMixin, Base):
    __tablename__ = "email_threads"
    __table_args__ = (UniqueConstraint("user_profile_id", "external_thread_id", name="uq_user_external_thread"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("application_leads.id", ondelete="SET NULL"))
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255))
    participant_emails: Mapped[list[str] | None] = mapped_column(JSON)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (UniqueConstraint("thread_id", "external_message_id", name="uq_thread_external_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("application_leads.id", ondelete="SET NULL"))
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    from_email: Mapped[str | None] = mapped_column(String(255))
    to_emails: Mapped[list[str] | None] = mapped_column(JSON)
    cc_emails: Mapped[list[str] | None] = mapped_column(JSON)
    bcc_emails: Mapped[list[str] | None] = mapped_column(JSON)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str | None] = mapped_column(String(255))
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    is_incoming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Index("ix_email_threads_last_message_at", EmailThread.last_message_at)
Index("ix_email_messages_reply_at", EmailMessage.reply_at)
