from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.constants import PAGE_META
from app.db.session import get_db
from app.services.lead_service import LeadService
from app.services.search_service import SearchService
from app.web.deps import get_search_service
from app.web.views import render_page

router = APIRouter(tags=["web-dashboard"])


def get_lead_service() -> LeadService:
    return LeadService()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    db: Session = Depends(get_db),
    lead_service: LeadService = Depends(get_lead_service),
    search_service: SearchService = Depends(get_search_service),
) -> HTMLResponse:
    meta = PAGE_META["dashboard"]
    # Read-only read-models: те же вызовы, что уже используют GET /leads и GET /jobs.
    dashboard = lead_service.build_dashboard_data(db)
    return render_page(
        request,
        "pages/dashboard.html",
        page_key="dashboard",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
        extra_context={
            "dashboard": dashboard,
            "source_descriptors": search_service.list_sources(),
        },
    )
