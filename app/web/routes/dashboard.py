from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.constants import PAGE_META
from app.web.views import render_page

router = APIRouter(tags=["web-dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    meta = PAGE_META["dashboard"]
    return render_page(
        request,
        "pages/dashboard.html",
        page_key="dashboard",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
    )
