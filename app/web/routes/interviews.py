from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.constants import PAGE_META
from app.web.views import render_page

router = APIRouter(tags=["web-interviews"])


@router.get("/interviews", response_class=HTMLResponse)
def interviews_page(request: Request) -> HTMLResponse:
    meta = PAGE_META["interviews"]
    return render_page(
        request,
        "pages/interviews.html",
        page_key="interviews",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
    )
