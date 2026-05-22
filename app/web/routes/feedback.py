from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.logging import logger
from app.db.session import get_db
from app.services.relevance_feedback_service import RelevanceFeedbackService
from app.services.relevance_memory_service import RelevanceMemoryService
from app.web.form_utils import read_form_data
from app.web.views import render_page

router = APIRouter(tags=["web-feedback"])

_feedback_service = RelevanceFeedbackService()
_memory_service = RelevanceMemoryService()

LABEL_DISPLAY: dict[str, str] = {
    "relevant": "Подходит",
    "weak": "Слабо подходит",
    "irrelevant": "Не подходит",
}

_VALID_LABELS = frozenset(LABEL_DISPLAY)


@router.post("/feedback/record", response_class=HTMLResponse)
async def feedback_record(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX endpoint: record per-vacancy relevance feedback, return inline acknowledgment."""
    form_data: FormData = await read_form_data(request)

    raw_profile_id = form_data.get("profile_id")
    canonical_key = str(form_data.get("canonical_key", "")).strip()
    source_id = str(form_data.get("source_id", "")).strip() or None
    source_name = str(form_data.get("source_name", "")).strip()
    normalized_title = str(form_data.get("normalized_title", "")).strip()
    company_name = str(form_data.get("company_name", "")).strip() or None
    location_text = str(form_data.get("location_text", "")).strip() or None
    role_family = str(form_data.get("role_family", "")).strip() or None
    current_query = str(form_data.get("current_query", "")).strip() or None
    feedback_label = str(form_data.get("feedback_label", "")).strip()

    if not canonical_key or not source_name or feedback_label not in _VALID_LABELS:
        return HTMLResponse('<span class="muted">Ошибка при сохранении оценки.</span>')

    try:
        profile_id = int(str(raw_profile_id).strip()) if raw_profile_id else None
    except (ValueError, TypeError):
        profile_id = None

    if profile_id is None:
        return HTMLResponse(
            '<span class="muted">Профиль не выбран — оценка не сохранена.</span>'
        )

    try:
        _feedback_service.record_feedback(
            db,
            profile_id=profile_id,
            canonical_key=canonical_key,
            source_id=source_id,
            source_name=source_name,
            normalized_title=normalized_title or "—",
            company_name=company_name,
            location_text=location_text,
            role_family=role_family,
            current_query=current_query,
            feedback_label=feedback_label,
        )
        db.commit()
    except Exception:
        logger.exception(
            "feedback_record_failed profile_id=%s canonical_key=%s",
            profile_id,
            canonical_key,
        )
        return HTMLResponse('<span class="muted">Не удалось сохранить оценку.</span>')

    label_display = LABEL_DISPLAY.get(feedback_label, feedback_label)
    return HTMLResponse(f'<span class="badge-success">✓ {label_display}</span>')


@router.get("/feedback/profile/{profile_id}", response_class=HTMLResponse)
def feedback_profile_summary(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Profile feedback memory summary page."""
    recent_feedback = _feedback_service.list_profile_feedback(db, profile_id=profile_id)
    memory = _memory_service.build_profile_memory(db, profile_id=profile_id)

    return render_page(
        request,
        "feedback/profile_feedback.html",
        page_key="jobs",
        page_title="Оценки вакансий",
        page_subtitle="Память профиля — что система знает о ваших предпочтениях.",
        extra_context={
            "profile_id": profile_id,
            "recent_feedback": recent_feedback,
            "memory": memory,
            "label_display": LABEL_DISPLAY,
        },
    )
