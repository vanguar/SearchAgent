from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.constants import PAGE_META, PAGE_UI_HINTS
from app.web.views import render_partial

router = APIRouter(tags=["web-htmx"])


@router.get("/htmx/placeholder/{page_key}", response_class=HTMLResponse)
def page_placeholder(request: Request, page_key: str) -> HTMLResponse:
    meta = PAGE_META.get(page_key, {"title": "Раздел", "subtitle": "Скоро здесь появится содержимое."})
    hints = PAGE_UI_HINTS.get(page_key, ("Содержимое раздела появится в следующих фазах.",))
    return render_partial(
        request,
        "partials/placeholder_fragment.html",
        page_key=page_key,
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
        extra_context={"hints": hints},
    )
