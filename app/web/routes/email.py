from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.config import Settings
from app.core.constants import PAGE_META
from app.core.logging import logger
from app.db.session import get_db
from app.services.email.base import EmailNotice, EmailValidationError
from app.services.email.gmail_sync import GmailSyncService
from app.services.email.linking_service import EmailLinkingService
from app.services.email.manual_email_ingest import ManualEmailIngestService
from app.services.email.workspace_service import EmailWorkspaceService
from app.web.form_utils import read_form_data
from app.web.views import render_page

router = APIRouter(tags=["web-email"])


def get_email_workspace_service() -> EmailWorkspaceService:
    return EmailWorkspaceService()


def get_manual_email_ingest_service() -> ManualEmailIngestService:
    return ManualEmailIngestService()


def get_email_linking_service() -> EmailLinkingService:
    return EmailLinkingService()


def get_gmail_sync_service() -> GmailSyncService:
    return GmailSyncService()


@router.get("/email", response_class=HTMLResponse)
def email_page(
    request: Request,
    thread_id: int | None = None,
    profile_id: int | None = None,
    notice_kind: str | None = None,
    notice_message: str | None = None,
    db: Session = Depends(get_db),
    workspace_service: EmailWorkspaceService = Depends(get_email_workspace_service),
) -> HTMLResponse:
    meta = PAGE_META["email"]
    notice = EmailNotice(kind=notice_kind, message_ru=notice_message) if notice_kind and notice_message else None
    workspace = workspace_service.build_workspace(
        db,
        selected_thread_id=thread_id,
        notice=notice,
        profile_id=profile_id,
    )
    return render_page(
        request,
        "pages/email.html",
        page_key="email",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
        extra_context={"workspace": workspace},
    )


@router.post("/email/import")
async def import_email_message(
    request: Request,
    db: Session = Depends(get_db),
    ingest_service: ManualEmailIngestService = Depends(get_manual_email_ingest_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    settings = Settings()
    known_user_emails = (settings.gmail_account_email.lower(),) if settings.gmail_account_email else ()
    try:
        payload = ingest_service.build_input(form_data)
    except EmailValidationError as exc:
        db.rollback()
        return _email_redirect(
            thread_id=None,
            notice_kind="warning",
            notice_message=exc.message_ru,
        )
    result = ingest_service.ingest(
        db,
        payload=payload,
        known_user_emails=known_user_emails,
    )
    if result.commit_required:
        db.commit()
    else:
        db.rollback()
    return _email_redirect(
        thread_id=result.redirect_thread_id,
        notice_kind=result.notice_kind,
        notice_message=result.notice_message_ru,
    )


@router.post("/email/threads/{thread_id}/link")
async def link_thread_to_lead(
    thread_id: int,
    request: Request,
    db: Session = Depends(get_db),
    linking_service: EmailLinkingService = Depends(get_email_linking_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    try:
        lead_id = _parse_optional_int(
            form_data.get("lead_id"),
            field_label_ru="лида",
            message_ru="Некорректный ID лида. Выберите лид из списка и попробуйте снова.",
        )
    except EmailValidationError as exc:
        db.rollback()
        return _email_redirect(
            thread_id=thread_id,
            notice_kind="warning",
            notice_message=exc.message_ru,
        )
    if lead_id is None:
        return _email_redirect(
            thread_id=thread_id,
            notice_kind="warning",
            notice_message="Выберите лид для привязки цепочки.",
        )
    result = linking_service.link_thread(db, thread_id=thread_id, lead_id=lead_id)
    if result.commit_required:
        db.commit()
    else:
        db.rollback()
    return _email_redirect(
        thread_id=result.redirect_thread_id or thread_id,
        notice_kind=result.notice_kind,
        notice_message=result.notice_message_ru,
    )


@router.post("/email/threads/{thread_id}/unlink")
def unlink_thread_from_lead(
    thread_id: int,
    db: Session = Depends(get_db),
    linking_service: EmailLinkingService = Depends(get_email_linking_service),
) -> RedirectResponse:
    result = linking_service.unlink_thread(db, thread_id=thread_id)
    if result.commit_required:
        db.commit()
    else:
        db.rollback()
    return _email_redirect(
        thread_id=result.redirect_thread_id or thread_id,
        notice_kind=result.notice_kind,
        notice_message=result.notice_message_ru,
    )


@router.post("/email/messages/{message_id}/link")
async def link_message_to_lead(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    linking_service: EmailLinkingService = Depends(get_email_linking_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    redirect_thread_id = _parse_helper_thread_id(form_data.get("thread_id"))
    try:
        lead_id = _parse_optional_int(
            form_data.get("lead_id"),
            field_label_ru="лида",
            message_ru="Некорректный ID лида. Выберите лид из списка и попробуйте снова.",
        )
    except EmailValidationError as exc:
        db.rollback()
        return _email_redirect(
            thread_id=redirect_thread_id,
            notice_kind="warning",
            notice_message=exc.message_ru,
        )
    if lead_id is None:
        return _email_redirect(
            thread_id=redirect_thread_id,
            notice_kind="warning",
            notice_message="Выберите лид для привязки письма.",
        )
    result = linking_service.link_message(db, message_id=message_id, lead_id=lead_id)
    if result.commit_required:
        db.commit()
    else:
        db.rollback()
    return _email_redirect(
        thread_id=result.redirect_thread_id,
        notice_kind=result.notice_kind,
        notice_message=result.notice_message_ru,
    )


@router.post("/email/messages/{message_id}/unlink")
async def unlink_message_from_lead(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    linking_service: EmailLinkingService = Depends(get_email_linking_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    redirect_thread_id = _parse_helper_thread_id(form_data.get("thread_id"))
    result = linking_service.unlink_message(db, message_id=message_id)
    if result.commit_required:
        db.commit()
    else:
        db.rollback()
    return _email_redirect(
        thread_id=result.redirect_thread_id or redirect_thread_id,
        notice_kind=result.notice_kind,
        notice_message=result.notice_message_ru,
    )


@router.post("/email/sync")
def run_gmail_sync_scaffold(
    sync_service: GmailSyncService = Depends(get_gmail_sync_service),
) -> RedirectResponse:
    result = sync_service.run_manual_sync()
    return _email_redirect(
        thread_id=None,
        notice_kind=result.notice_kind,
        notice_message=result.notice_message_ru,
    )


def _email_redirect(
    *,
    thread_id: int | None,
    notice_kind: str,
    notice_message: str,
) -> RedirectResponse:
    query_params = {"notice_kind": notice_kind, "notice_message": notice_message}
    if thread_id is not None:
        query_params["thread_id"] = str(thread_id)
    return RedirectResponse(url=f"/email?{urlencode(query_params)}", status_code=303)


def _parse_optional_int(
    value: object,
    *,
    field_label_ru: str,
    message_ru: str | None = None,
) -> int | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise EmailValidationError(message_ru or f"Некорректный ID {field_label_ru}.") from exc
    if parsed <= 0:
        raise EmailValidationError(message_ru or f"Некорректный ID {field_label_ru}.")
    return parsed


def _parse_helper_thread_id(value: object) -> int | None:
    try:
        return _parse_optional_int(value, field_label_ru="цепочки")
    except EmailValidationError:
        logger.info("email_route_invalid_helper_id field=thread_id")
        return None
