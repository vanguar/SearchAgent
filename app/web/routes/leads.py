from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.constants import PAGE_META
from app.core.logging import logger
from app.db.session import get_db
from app.services.application_tracker import ApplicationTrackerService
from app.services.cover_letter_service import CoverLetterService
from app.services.email.workspace_service import EmailWorkspaceService
from app.services.lead_service import LeadService, SearchLeadCandidate
from app.services.reminder_service import ReminderService
from app.services.resume_service import ResumeService
from app.web.form_utils import read_form_data
from app.web.views import render_page, render_partial

router = APIRouter(tags=["web-leads"])


def get_lead_service() -> LeadService:
    return LeadService()


def get_application_tracker_service() -> ApplicationTrackerService:
    return ApplicationTrackerService()


def get_resume_service() -> ResumeService:
    return ResumeService()


def get_cover_letter_service() -> CoverLetterService:
    return CoverLetterService()


def get_reminder_service() -> ReminderService:
    return ReminderService()


def get_email_workspace_service() -> EmailWorkspaceService:
    return EmailWorkspaceService()


@router.get("/leads", response_class=HTMLResponse)
def leads_page(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    resume_service: ResumeService = Depends(get_resume_service),
    cover_letter_service: CoverLetterService = Depends(get_cover_letter_service),
) -> HTMLResponse:
    meta = PAGE_META["leads"]
    dashboard = lead_service.build_dashboard_data(db)
    owner_context = dashboard.owner_context
    resumes = resume_service.list_resumes(db, user_profile_id=owner_context.user_profile_id) if owner_context else ()
    resume_versions = (
        resume_service.list_resume_versions(db, user_profile_id=owner_context.user_profile_id) if owner_context else ()
    )
    templates = (
        cover_letter_service.list_templates(db, user_profile_id=owner_context.user_profile_id) if owner_context else ()
    )
    return render_page(
        request,
        "pages/leads.html",
        page_key="leads",
        page_title=meta["title"],
        page_subtitle="Лиды, отклики, напоминания и документы в одном рабочем потоке.",
        extra_context={
            "dashboard": dashboard,
            "resume_catalog": resumes,
            "resume_version_catalog": resume_versions,
            "cover_letter_templates": templates,
        },
    )


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail_page(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    resume_service: ResumeService = Depends(get_resume_service),
    cover_letter_service: CoverLetterService = Depends(get_cover_letter_service),
    email_workspace_service: EmailWorkspaceService = Depends(get_email_workspace_service),
) -> HTMLResponse:
    detail = lead_service.build_detail_data(db, lead_id=lead_id)
    resume_versions = resume_service.list_resume_versions(db, user_profile_id=detail.lead.user_profile_id)
    templates = cover_letter_service.list_templates(db, user_profile_id=detail.lead.user_profile_id)
    email_summary = email_workspace_service.build_lead_email_summary(db, lead_id=detail.lead.id)
    return render_page(
        request,
        "leads/detail.html",
        page_key="leads",
        page_title=detail.lead.translated_title_ru or detail.lead.vacancy_title,
        page_subtitle="Полная карточка лида: документы, заметки, интервью и следующие шаги.",
        extra_context={
            "detail": detail,
            "resume_version_catalog": resume_versions,
            "cover_letter_templates": templates,
            "email_summary": email_summary,
        },
    )


@router.post("/leads/from-search", response_class=HTMLResponse)
async def create_lead_from_search(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> HTMLResponse:
    form_data: FormData = await read_form_data(request)
    action = _string_value(form_data, "action") or "save"
    profile_id = _parse_optional_int(_string_value(form_data, "profile_id") or None)

    try:
        result = lead_service.create_or_get_from_search_candidate(
            db,
            candidate=_build_search_candidate(form_data),
            action=action,
            profile_id=profile_id,
        )
        db.commit()
        if action == "save":
            message = "Лид добавлен в отклики." if result.created else "Эта вакансия уже была сохранена."
        else:
            message = "Просмотр зафиксирован." if result.created or result.lead.viewed_at else "Просмотр уже был отмечен."
        kind = "success"
    except ValueError as exc:
        db.rollback()
        result = None
        message = str(exc)
        kind = "warning"

    return render_partial(
        request,
        "leads/partials/search_action_feedback.html",
        page_key="leads",
        page_title=PAGE_META["leads"]["title"],
        page_subtitle=PAGE_META["leads"]["subtitle"],
        extra_context={
            "action_result": result,
            "feedback_message": message,
            "feedback_kind": kind,
        },
    )


@router.post("/leads/open-original")
async def open_original_from_search(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> Response:
    form_data: FormData = await read_form_data(request)
    original_url = _empty_to_none(_string_value(form_data, "original_url"))
    profile_id = _parse_optional_int(_string_value(form_data, "profile_id") or None)

    try:
        candidate = _build_search_candidate(form_data)
        result = lead_service.create_or_get_from_search_candidate(
            db,
            candidate=candidate,
            action="opened_original_link",
            profile_id=profile_id,
        )
        db.commit()
        redirect_url = result.lead.original_url or candidate.original_url or "/jobs"
        return RedirectResponse(url=redirect_url, status_code=303)
    except ValueError as exc:
        db.rollback()
        if original_url:
            logger.info(
                "lead_open_original_tracking_skipped reason=%s original_url=%s",
                _open_original_skip_reason(exc),
                original_url,
            )
            return RedirectResponse(url=original_url, status_code=303)
        logger.warning("lead_open_original_failed reason=invalid_request error=%s", exc)
        return HTMLResponse(
            "Не удалось открыть оригинал вакансии: ссылка отсутствует или данные вакансии неполные.",
            status_code=400,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        if original_url:
            logger.warning(
                "lead_open_original_tracking_skipped reason=db_unavailable original_url=%s error=%s",
                original_url,
                exc.__class__.__name__,
            )
            return RedirectResponse(url=original_url, status_code=303)
        logger.exception("lead_open_original_failed")
        return HTMLResponse(
            "Не удалось открыть оригинал вакансии: база откликов временно недоступна, и ссылка не была передана.",
            status_code=503,
        )


@router.post("/leads/{lead_id}/open-original")
async def open_original_from_lead(
    request: Request,
    lead_id: int,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> Response:
    form_data: FormData = await read_form_data(request)
    original_url = _empty_to_none(_string_value(form_data, "original_url"))

    try:
        lead = lead_service.get_lead(db, lead_id=lead_id)
        redirect_url = lead.original_url or original_url
        if not redirect_url:
            logger.warning("lead_open_original_failed reason=missing_original_url lead_id=%s", lead_id)
            return HTMLResponse(
                "Не удалось открыть оригинал вакансии: ссылка для этой карточки не сохранена.",
                status_code=404,
            )
        lead_service.lead_event_service.record_event(
            db,
            lead,
            "opened_original_link",
            payload={"vacancy_title": lead.vacancy_title, "original_url": lead.original_url},
            allow_repeat=True,
        )
        db.commit()
        return RedirectResponse(url=redirect_url, status_code=303)
    except SQLAlchemyError as exc:
        db.rollback()
        if original_url:
            logger.warning(
                "lead_open_original_tracking_skipped reason=db_unavailable lead_id=%s error=%s",
                lead_id,
                exc.__class__.__name__,
            )
            return RedirectResponse(url=original_url, status_code=303)
        logger.warning(
            "lead_open_original_failed reason=db_unavailable_without_redirect lead_id=%s error=%s",
            lead_id,
            exc.__class__.__name__,
        )
        return HTMLResponse(
            "Не удалось открыть оригинал вакансии: база откликов временно недоступна, и ссылка не была передана.",
            status_code=503,
        )
    except LookupError as exc:
        db.rollback()
        if original_url:
            logger.info(
                "lead_open_original_tracking_skipped reason=lead_not_found lead_id=%s",
                lead_id,
            )
            return RedirectResponse(url=original_url, status_code=303)
        logger.warning("lead_open_original_failed reason=lead_not_found lead_id=%s error=%s", lead_id, exc)
        return HTMLResponse(
            "Не удалось открыть оригинал вакансии: карточка лида не найдена, и ссылка не была передана.",
            status_code=404,
        )


@router.post("/leads/{lead_id}/details")
async def update_lead_details(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    lead = lead_service.get_lead(db, lead_id=lead_id)
    lead_service.update_manual_details(
        db,
        lead=lead,
        application_channel=_empty_to_none(_string_value(form_data, "application_channel")),
        salary_expectation_text=_empty_to_none(_string_value(form_data, "salary_expectation_text")),
        contact_person=_empty_to_none(_string_value(form_data, "contact_person")),
        contact_email=_empty_to_none(_string_value(form_data, "contact_email")),
        follow_up_due_at=_parse_optional_date(_string_value(form_data, "follow_up_due_at")),
        follow_up_sent_at=_parse_optional_date(_string_value(form_data, "follow_up_sent_at")),
        next_action_at=_parse_optional_datetime(_string_value(form_data, "next_action_at")),
        resume_version_id=_parse_optional_int(_string_value(form_data, "resume_version_id")),
        cover_letter_template_id=_parse_optional_int(_string_value(form_data, "cover_letter_template_id")),
    )
    db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/apply")
async def mark_applied(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    tracker_service: ApplicationTrackerService = Depends(get_application_tracker_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    lead = lead_service.get_lead(db, lead_id=lead_id)
    tracker_service.mark_applied(
        db,
        lead=lead,
        applied_at=_parse_optional_date(_string_value(form_data, "applied_at")),
        application_channel=_empty_to_none(_string_value(form_data, "application_channel")),
        salary_expectation_text=_empty_to_none(_string_value(form_data, "salary_expectation_text")),
        contact_person=_empty_to_none(_string_value(form_data, "contact_person")),
        contact_email=_empty_to_none(_string_value(form_data, "contact_email")),
        follow_up_due_at=_parse_optional_date(_string_value(form_data, "follow_up_due_at")),
        follow_up_sent_at=_parse_optional_date(_string_value(form_data, "follow_up_sent_at")),
        resume_version_id=_parse_optional_int(_string_value(form_data, "resume_version_id")),
        cover_letter_template_id=_parse_optional_int(_string_value(form_data, "cover_letter_template_id")),
    )
    db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/reply")
async def mark_reply_received(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    tracker_service: ApplicationTrackerService = Depends(get_application_tracker_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    lead = lead_service.get_lead(db, lead_id=lead_id)
    tracker_service.mark_reply_received(
        db,
        lead=lead,
        reply_at=_parse_optional_date(_string_value(form_data, "reply_at")),
        details=_empty_to_none(_string_value(form_data, "reply_details")),
    )
    db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/reject")
async def mark_rejected(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    tracker_service: ApplicationTrackerService = Depends(get_application_tracker_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    lead = lead_service.get_lead(db, lead_id=lead_id)
    tracker_service.mark_rejected(
        db,
        lead=lead,
        rejected_at=_parse_optional_date(_string_value(form_data, "rejected_at")),
        reason=_empty_to_none(_string_value(form_data, "rejection_reason")),
    )
    db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/interview")
async def schedule_interview(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    tracker_service: ApplicationTrackerService = Depends(get_application_tracker_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    interview_at = _parse_optional_datetime(_string_value(form_data, "interview_at"))
    if interview_at is None:
        return _lead_redirect(lead_id)
    lead = lead_service.get_lead(db, lead_id=lead_id)
    tracker_service.schedule_interview(
        db,
        lead=lead,
        interview_at=interview_at,
        interview_format=_empty_to_none(_string_value(form_data, "interview_format")),
        interviewer_name=_empty_to_none(_string_value(form_data, "interviewer_name")),
        location=_empty_to_none(_string_value(form_data, "interview_location")),
        notes=_empty_to_none(_string_value(form_data, "interview_notes")),
    )
    db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/archive")
async def archive_lead(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    tracker_service: ApplicationTrackerService = Depends(get_application_tracker_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    lead = lead_service.get_lead(db, lead_id=lead_id)
    tracker_service.archive_lead(
        db,
        lead=lead,
        archived_at=_parse_optional_date(_string_value(form_data, "archived_at")),
        note=_empty_to_none(_string_value(form_data, "archive_note")),
    )
    db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/notes")
async def add_note(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    note_body = _empty_to_none(_string_value(form_data, "note_body"))
    if note_body:
        lead = lead_service.get_lead(db, lead_id=lead_id)
        lead_service.add_note(
            db,
            lead=lead,
            body=note_body,
            pinned=_string_value(form_data, "note_pinned") == "on",
        )
        db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/{lead_id}/reminders")
async def add_reminder(
    lead_id: int,
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    reminder_service: ReminderService = Depends(get_reminder_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    title = _empty_to_none(_string_value(form_data, "reminder_title"))
    if title:
        lead = lead_service.get_lead(db, lead_id=lead_id)
        reminder_service.create_reminder(
            db,
            lead=lead,
            title=title,
            due_at=_parse_optional_datetime(_string_value(form_data, "reminder_due_at")),
            details=_empty_to_none(_string_value(form_data, "reminder_details")),
        )
        db.commit()
    return _lead_redirect(lead_id)


@router.post("/leads/library/resumes")
async def create_resume(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    resume_service: ResumeService = Depends(get_resume_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    owner_context = lead_service.resolve_owner_context(db)
    if owner_context is not None:
        title = _empty_to_none(_string_value(form_data, "resume_title"))
        if title:
            resume_service.create_resume(
                db,
                user_profile_id=owner_context.user_profile_id,
                title=title,
                language=_empty_to_none(_string_value(form_data, "resume_language")),
            )
            db.commit()
    return RedirectResponse(url="/leads", status_code=303)


@router.post("/leads/library/resume-versions")
async def create_resume_version(
    request: Request,
    db: Session = Depends(get_db),
    resume_service: ResumeService = Depends(get_resume_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    resume_id = _parse_optional_int(_string_value(form_data, "resume_id"))
    version_label = _empty_to_none(_string_value(form_data, "version_label"))
    if resume_id is not None and version_label:
        resume_service.create_version(
            db,
            resume_id=resume_id,
            version_label=version_label,
            file_path=_empty_to_none(_string_value(form_data, "file_path")),
            storage_hint=_empty_to_none(_string_value(form_data, "storage_hint")),
            is_default=_string_value(form_data, "is_default") == "on",
        )
        db.commit()
    return RedirectResponse(url="/leads", status_code=303)


@router.post("/leads/library/templates")
async def create_cover_letter_template(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    cover_letter_service: CoverLetterService = Depends(get_cover_letter_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    owner_context = lead_service.resolve_owner_context(db)
    name = _empty_to_none(_string_value(form_data, "template_name"))
    body = _empty_to_none(_string_value(form_data, "template_body"))
    if owner_context is not None and name and body:
        cover_letter_service.create_template(
            db,
            user_profile_id=owner_context.user_profile_id,
            name=name,
            template_body=body,
            language=_empty_to_none(_string_value(form_data, "template_language")),
        )
        db.commit()
    return RedirectResponse(url="/leads", status_code=303)


def _lead_redirect(lead_id: int) -> RedirectResponse:
    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


def _build_search_candidate(form_data: Mapping[str, object]) -> SearchLeadCandidate:
    vacancy_title = _empty_to_none(_string_value(form_data, "vacancy_title"))
    source_id = _empty_to_none(_string_value(form_data, "source_id"))
    source_name = _empty_to_none(_string_value(form_data, "source_name"))
    source_external_id = _empty_to_none(_string_value(form_data, "source_external_id"))
    if not vacancy_title or not source_id or not source_name or not source_external_id:
        raise ValueError("Не удалось распознать вакансию для сохранения.")

    return SearchLeadCandidate(
        source_id=source_id,
        source_name=source_name,
        source_external_id=source_external_id,
        vacancy_title=vacancy_title,
        original_url=_empty_to_none(_string_value(form_data, "original_url")),
        canonical_key=_empty_to_none(_string_value(form_data, "canonical_key")),
        translated_title_ru=_empty_to_none(_string_value(form_data, "translated_title_ru")),
        company_name=_empty_to_none(_string_value(form_data, "company_name")),
        location_text=_empty_to_none(_string_value(form_data, "location_text")),
        summary_ru=_empty_to_none(_string_value(form_data, "summary_ru")),
        bucket=_empty_to_none(_string_value(form_data, "bucket")),
    )


def _string_value(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key, "")
    return str(value).strip()


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _parse_optional_int(value: str | None) -> int | None:
    if not value:
        return None
    return int(value)


def _parse_optional_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _open_original_skip_reason(error: ValueError) -> str:
    message = str(error).casefold()
    if "профиль" in message:
        return "owner_context_unavailable"
    if "ваканси" in message:
        return "candidate_unavailable"
    return "validation_error"
