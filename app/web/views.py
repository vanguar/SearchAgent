from __future__ import annotations

import html

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import Settings
from app.core.constants import APP_SUBTITLE, APP_TITLE, NAV_ITEMS
from app.services.llm_client import LLMStatus, get_runtime_status

settings = Settings()
templates = Jinja2Templates(directory=str(settings.templates_dir))
# Presentation-only helper: decode HTML entities (e.g. double-encoded "&amp;")
# in visible text. Jinja autoescaping still re-escapes the result on render,
# so this is safe — it never bypasses escaping and never touches stored data.
templates.env.filters["unescape"] = html.unescape


def _display_llm_status() -> str:
    """Return UI status without forcing an OpenAI request on ordinary pages."""
    status = get_runtime_status()
    current_settings = Settings()
    if not current_settings.openai_api_key:
        return LLMStatus.MISSING_CONFIG.value
    if status == LLMStatus.MISSING_CONFIG:
        return LLMStatus.CONFIGURED.value
    return status.value


def render_page(
    request: Request,
    template_name: str,
    *,
    page_key: str,
    page_title: str,
    page_subtitle: str,
    extra_context: dict[str, object] | None = None,
) -> HTMLResponse:
    """Render a page with shared Russian UI context."""
    context: dict[str, object] = {
        "app_title": APP_TITLE,
        "app_subtitle": APP_SUBTITLE,
        "nav_items": NAV_ITEMS,
        "active_page": page_key,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
        "llm_status": _display_llm_status(),
    }
    if extra_context:
        context.update(extra_context)

    return templates.TemplateResponse(request=request, name=template_name, context=context)


def render_partial(
    request: Request,
    template_name: str,
    *,
    page_key: str,
    page_title: str,
    page_subtitle: str,
    extra_context: dict[str, object] | None = None,
) -> HTMLResponse:
    """Render an HTMX partial with common context."""
    context: dict[str, object] = {
        "active_page": page_key,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
    }
    if extra_context:
        context.update(extra_context)

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
    )
