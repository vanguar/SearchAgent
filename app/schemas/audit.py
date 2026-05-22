from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class UserActionAuditSchema(ORMBaseSchema):
    id: int
    user_profile_id: int | None = None
    lead_id: int | None = None
    action_name: str
    action_target: str | None = None
    action_payload: dict | None = None
    created_at: datetime
