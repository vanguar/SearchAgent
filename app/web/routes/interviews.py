from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.constants import PAGE_META
from app.db.session import get_db
from app.services.lead_service import LeadService
from app.web.views import render_page

router = APIRouter(tags=["web-interviews"])


def get_lead_service() -> LeadService:
    return LeadService()


@router.get("/interviews", response_class=HTMLResponse)
def interviews_page(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
) -> HTMLResponse:
    meta = PAGE_META["interviews"]
    # Read-only read-model: the same call GET /dashboard already uses.
    dashboard = lead_service.build_dashboard_data(db)
    return render_page(
        request,
        "pages/interviews.html",
        page_key="interviews",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
        extra_context={"dashboard": dashboard},
    )
