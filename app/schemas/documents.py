from __future__ import annotations

from datetime import datetime

from app.schemas.base import ORMBaseSchema


class ResumeSchema(ORMBaseSchema):
    id: int
    user_profile_id: int
    title: str
    language: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ResumeVersionSchema(ORMBaseSchema):
    id: int
    resume_id: int
    version_label: str
    file_path: str | None = None
    storage_hint: str | None = None
    checksum: str | None = None
    is_default: bool
    created_at: datetime
    updated_at: datetime


class CoverLetterTemplateSchema(ORMBaseSchema):
    id: int
    user_profile_id: int
    name: str
    language: str | None = None
    template_body: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
