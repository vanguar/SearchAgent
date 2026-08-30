from __future__ import annotations

import html
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import Settings
from app.core.constants import APP_SUBTITLE, APP_TITLE, NAV_ITEMS
from app.core.time import utc_now
from app.services.llm_client import LLMStatus, get_runtime_status

settings = Settings()
templates = Jinja2Templates(directory=str(settings.templates_dir))
# Presentation-only helper: decode HTML entities (e.g. double-encoded "&amp;")
# in visible text. Jinja autoescaping still re-escapes the result on render,
# so this is safe — it never bypasses escaping and never touches stored data.
templates.env.filters["unescape"] = html.unescape


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


def _human_until(value: datetime | None) -> str:
    """Presentation-only relative day label (e.g. «через 4 дня», «сегодня»).

    Normalizes naive datetimes (SQLite may drop tzinfo) to UTC before comparing,
    so it never raises on mixed-awareness values.
    """
    if value is None:
        return ""
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    days = (normalized.astimezone(UTC).date() - utc_now().date()).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == -1:
        return "вчера"
    if days > 1:
        return f"через {days} {_plural_days(days)}"
    return f"{abs(days)} {_plural_days(days)} назад"


templates.env.filters["human_until"] = _human_until


def _is_upcoming_interview(value: datetime | None) -> bool:
    """Presentation-only test: interview is today or in the future (by day)."""
    if value is None:
        return False
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).date() >= utc_now().date()


templates.env.tests["upcoming_interview"] = _is_upcoming_interview

_RU_MONTH_ABBR = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)


def _ru_month(value: datetime | None) -> str:
    """Presentation-only Russian short month for a datetime (e.g. «сен»)."""
    if value is None:
        return ""
    return _RU_MONTH_ABBR[value.month - 1]


templates.env.filters["ru_month"] = _ru_month


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
