from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.config import Settings
from app.core.constants import PAGE_META
from app.services.llm_client import LLMStatus, build_llm_client, get_runtime_status
from app.web.views import render_page

router = APIRouter(tags=["web-settings"])

_LLM_STATUS_LABELS: dict[LLMStatus, dict[str, str]] = {
    LLMStatus.CONFIGURED: {
        "label": "ИИ настроен",
        "detail": "OPENAI_API_KEY задан, клиент готов к запросам.",
        "css_class": "text-green-700",
    },
    LLMStatus.EXTRACTION_SUCCEEDED: {
        "label": "ИИ-разбор успешен",
        "detail": "Последний разбор профиля через OpenAI завершился успешно.",
        "css_class": "text-green-700",
    },
    LLMStatus.MISSING_CONFIG: {
        "label": "ИИ отключён",
        "detail": "OPENAI_API_KEY не задан. Система работает в детерминированном режиме.",
        "css_class": "text-yellow-700",
    },
    LLMStatus.PROVIDER_ERROR: {
        "label": "ИИ-запрос не прошёл",
        "detail": "Последний вызов OpenAI упал или истёк по timeout. Система использует локальный fallback.",
        "css_class": "text-red-700",
    },
}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    meta = PAGE_META["settings"]
    settings = Settings()
    # Вызываем build_llm_client, чтобы при отсутствии ключа обновить _runtime_status.
    # Не пересоздаём клиент просто для проверки статуса — используем модульную переменную.
    build_llm_client(settings)
    status = get_runtime_status()
    status_info = _LLM_STATUS_LABELS[status]

    return render_page(
        request,
        "pages/settings.html",
        page_key="settings",
        page_title=meta["title"],
        page_subtitle=meta["subtitle"],
        extra_context={
            "llm_status": status,
            "llm_status_label": status_info["label"],
            "llm_status_detail": status_info["detail"],
            "llm_status_css": status_info["css_class"],
            "llm_model": settings.openai_model,
        },
    )
