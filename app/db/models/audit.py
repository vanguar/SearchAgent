from __future__ import annotations

from datetime import datetime

from app.db.base import Base
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column


class UserActionAudit(Base):
    __tablename__ = "user_action_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int | None] = mapped_column(ForeignKey("user_profiles.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("application_leads.id", ondelete="SET NULL"))
    action_name: Mapped[str] = mapped_column(String(120), nullable=False)
    action_target: Mapped[str | None] = mapped_column(String(255))
    action_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Index("ix_user_action_audits_created_at", UserActionAudit.created_at)
