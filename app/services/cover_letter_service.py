from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.documents import CoverLetterTemplate


@dataclass(frozen=True, slots=True)
class CoverLetterTemplateOption:
    id: int
    name: str
    language: str | None


class CoverLetterService:
    """Reusable cover letter templates linked to application leads."""

    def create_template(
        self,
        session: Session,
        *,
        user_profile_id: int,
        name: str,
        template_body: str,
        language: str | None = None,
    ) -> CoverLetterTemplate:
        normalized_name = name.strip()
        normalized_body = template_body.strip()
        if not normalized_name:
            raise ValueError("Название шаблона обязательно.")
        if not normalized_body:
            raise ValueError("Текст шаблона обязателен.")
        template = CoverLetterTemplate(
            user_profile_id=user_profile_id,
            name=normalized_name,
            language=(language or "").strip() or None,
            template_body=normalized_body,
            is_active=True,
        )
        session.add(template)
        session.flush()
        return template

    def list_templates(self, session: Session, *, user_profile_id: int) -> tuple[CoverLetterTemplateOption, ...]:
        templates = session.execute(
            select(CoverLetterTemplate)
            .where(CoverLetterTemplate.user_profile_id == user_profile_id)
            .order_by(CoverLetterTemplate.is_active.desc(), CoverLetterTemplate.updated_at.desc(), CoverLetterTemplate.id.desc())
        ).scalars()
        return tuple(
            CoverLetterTemplateOption(id=template.id, name=template.name, language=template.language)
            for template in templates
        )
