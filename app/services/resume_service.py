from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.documents import Resume, ResumeVersion


@dataclass(frozen=True, slots=True)
class ResumeOption:
    id: int
    title: str
    language: str | None


@dataclass(frozen=True, slots=True)
class ResumeVersionOption:
    id: int
    label_ru: str
    resume_id: int


class ResumeService:
    """Manual resume storage and version selection for leads."""

    def create_resume(
        self,
        session: Session,
        *,
        user_profile_id: int,
        title: str,
        language: str | None = None,
    ) -> Resume:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Название резюме обязательно.")
        resume = Resume(
            user_profile_id=user_profile_id,
            title=normalized_title,
            language=(language or "").strip() or None,
            is_active=True,
        )
        session.add(resume)
        session.flush()
        return resume

    def create_version(
        self,
        session: Session,
        *,
        resume_id: int,
        version_label: str,
        file_path: str | None = None,
        storage_hint: str | None = None,
        checksum: str | None = None,
        is_default: bool = False,
    ) -> ResumeVersion:
        resume = session.get(Resume, resume_id)
        if resume is None:
            raise ValueError("Базовое резюме не найдено.")
        normalized_label = version_label.strip()
        if not normalized_label:
            raise ValueError("Название версии обязательно.")
        if is_default:
            for version in session.execute(
                select(ResumeVersion).where(ResumeVersion.resume_id == resume_id)
            ).scalars():
                version.is_default = False

        version = ResumeVersion(
            resume_id=resume_id,
            version_label=normalized_label,
            file_path=(file_path or "").strip() or None,
            storage_hint=(storage_hint or "").strip() or None,
            checksum=(checksum or "").strip() or None,
            is_default=is_default,
        )
        session.add(version)
        session.flush()
        return version

    def list_resumes(self, session: Session, *, user_profile_id: int) -> tuple[ResumeOption, ...]:
        resumes = session.execute(
            select(Resume)
            .where(Resume.user_profile_id == user_profile_id)
            .order_by(Resume.is_active.desc(), Resume.updated_at.desc(), Resume.id.desc())
        ).scalars()
        return tuple(
            ResumeOption(id=resume.id, title=resume.title, language=resume.language)
            for resume in resumes
        )

    def list_resume_versions(self, session: Session, *, user_profile_id: int) -> tuple[ResumeVersionOption, ...]:
        resumes = {
            resume.id: resume
            for resume in session.execute(
                select(Resume).where(Resume.user_profile_id == user_profile_id)
            ).scalars()
        }
        versions = session.execute(
            select(ResumeVersion).order_by(ResumeVersion.is_default.desc(), ResumeVersion.updated_at.desc(), ResumeVersion.id.desc())
        ).scalars()
        options: list[ResumeVersionOption] = []
        for version in versions:
            resume = resumes.get(version.resume_id)
            if resume is None:
                continue
            label = f"{resume.title} · {version.version_label}"
            if version.file_path:
                label = f"{label} ({version.file_path})"
            options.append(
                ResumeVersionOption(
                    id=version.id,
                    label_ru=label,
                    resume_id=resume.id,
                )
            )
        return tuple(options)
