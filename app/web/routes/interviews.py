from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.constants import PAGE_META
from app.core.time import utc_now
from app.db.session import get_db
from app.services.lead_service import LeadService
from app.web.views import render_page

router = APIRouter(tags=["web-interviews"])

_RU_MONTH_ABBR = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)


@dataclass(frozen=True, slots=True)
class InterviewRow:
    """Presentation-only view of one scheduled interview (from lead.interview_at)."""

    lead_id: int
    title: str
    company: str
    source: str | None
    status: str | None
    day: str
    month: str
    date_short: str
    time: str
    when_label: str


def get_lead_service() -> LeadService:
    return LeadService()


def _normalize(value: datetime) -> datetime:
    """Coerce naive datetimes (SQLite may drop tzinfo) to UTC — never raises."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _plural_days(n: int) -> str:
    n = abs(n)
    if 11 <= n % 100 <= 14:
        return "дней"
    tail = n % 10
    if tail == 1:
        return "день"
    if 2 <= tail <= 4:
        return "дня"
    return "дней"


def _human_until(value: datetime, now: datetime) -> str:
    days = (value.astimezone(UTC).date() - now.date()).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == -1:
        return "вчера"
    if days > 1:
        return f"через {days} {_plural_days(days)}"
    return f"{abs(days)} {_plural_days(days)} назад"


def _build_row(card: object, now: datetime) -> InterviewRow:
    lead = card.lead  # type: ignore[attr-defined]
    when = _normalize(lead.interview_at)
    return InterviewRow(
        lead_id=lead.id,
        title=lead.translated_title_ru or lead.vacancy_title,
        company=lead.company_name or "Компания не указана",
        source=lead.source_name,
        status=card.status_label_ru,  # type: ignore[attr-defined]
        day=when.strftime("%d"),
        month=_RU_MONTH_ABBR[when.month - 1],
        date_short=when.strftime("%d.%m"),
        time=when.strftime("%H:%M"),
        when_label=_human_until(when, now),
    )


@router.get("/interviews", response_class=HTMLResponse)
def interviews_page(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> HTMLResponse:
    meta = PAGE_META["interviews"]
    now = utc_now().astimezone(UTC)
    # Read-only read-model: the same call GET /dashboard already uses.
    dashboard = lead_service.build_dashboard_data(db)

    scheduled = [card for card in dashboard.leads if card.lead.interview_at is not None]
    scheduled.sort(key=lambda card: _normalize(card.lead.interview_at))
    today = now.date()
    upcoming = [_build_row(card, now) for card in scheduled if _normalize(card.lead.interview_at).date() >= today]
    past = [_build_row(card, now) for card in reversed(scheduled) if _normalize(card.lead.interview_at).date() < today]

    return render_page(
        request,
        "pages/interviews.html",
        page_key="interviews",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
        extra_context={
            "warning_message": dashboard.warning_message,
            "upcoming": upcoming,
            "past": past,
        },
    )
