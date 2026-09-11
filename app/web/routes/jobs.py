from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Mapping

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData

from app.core.constants import PAGE_META
from app.core.logging import logger
from app.core.time import utc_now
from app.db.session import get_db
from app.services.profile_catalog_service import ProfileCatalogService
from app.services.relevance_memory_service import ProfileFeedbackMemory, RelevanceMemoryService
from app.services.search_export_service import (
    EXPORT_MODE_DIAGNOSTICS,
    EXPORT_MODES,
    build_export_filename,
    build_search_export,
)
from app.services.search_history_service import SearchHistoryService
from app.services.search_models import SearchRunResult
from app.services.search_normalizer import (
    is_remote_worldwide_location,
    normalize_location,
    normalize_query_from_roles,
)
from app.services.search_service import SearchService
from app.services.source_adapters.models import SourceAdapterDescriptor, SourceSearchInput
from app.web.deps import get_profile_catalog_service, get_search_service
from app.web.form_utils import read_form_data
from app.web.views import render_page, render_partial, templates

# ---------------------------------------------------------------------------
# Фоновый поиск — in-memory реестр задач (для single-user локального приложения)
# ---------------------------------------------------------------------------

_STOP_BUTTON_DELAY_SECONDS = 5   # через сколько секунд показывать кнопку остановки
_TASK_MAX_AGE_SECONDS = 600      # задачи старше 10 минут удаляются автоматически
SOURCE_SCOPE_WESTERN = "western"
SOURCE_SCOPE_RUSSIAN = "russian"
RUSSIAN_LANGUAGE_SOURCE_IDS = frozenset({"hh", "dou_rss", "djinni_rss"})


class _SearchTask:
    """Состояние фоновой задачи поиска. Thread-safe через Lock."""

    def __init__(
        self,
        task_id: str,
        profile_id: int | None,
        *,
        search_input: SourceSearchInput | None = None,
        source_ids: tuple[str, ...] = (),
    ) -> None:
        self.task_id = task_id
        self.profile_id = profile_id
        # Хранятся только ради диагностической JSON-выгрузки — сам поиск их не перечитывает.
        self.search_input = search_input
        self.source_ids = source_ids
        self.status = "running"    # "running" | "stopping" | "done" | "error"
        self.phase_code = "attempt"   # "attempt" | "fetch" | "postprocess"
        self.phase_label = "Подготовка поиска"
        self.current_query: str | None = None
        self.current_count = 0
        self.total_count = 0
        self.best_count = 0
        self.last_source: str | None = None
        self.partial_result: SearchRunResult | None = None
        self.result: SearchRunResult | None = None
        self.error_message: str | None = None
        self.stop_event = threading.Event()
        self.created_at = time.monotonic()
        self._attempt_index = -1
        self._attempt_counts: dict[int, int] = {}
        self._lock = threading.Lock()

    def on_attempt_started(self, query: str | None) -> None:
        with self._lock:
            self._attempt_index += 1
            self._attempt_counts.setdefault(self._attempt_index, 0)
            self.phase_code = "attempt"
            self.phase_label = "Новая попытка поиска"
            self.current_query = query
            self.current_count = 0
            self.last_source = None

    def on_source_done(self, found_count: int, source_name: str, query: str | None = None) -> None:
        with self._lock:
            self._record_attempt_count(found_count)
            self.phase_code = "fetch"
            self.phase_label = "Поиск по источникам"
            self.current_query = query
            self.current_count = found_count
            self.best_count = max(self.best_count, found_count)
            self.total_count = sum(self._attempt_counts.values())
            self.last_source = source_name

    def on_postprocess(self, found_count: int, query: str | None = None) -> None:
        with self._lock:
            self._record_attempt_count(found_count)
            self.phase_code = "postprocess"
            self.phase_label = "Дедупликация и фильтрация"
            self.current_query = query
            self.current_count = found_count
            self.best_count = max(self.best_count, found_count)
            self.total_count = sum(self._attempt_counts.values())
            self.last_source = None

    def mark_done(self, result: SearchRunResult) -> None:
        with self._lock:
            self.result = result
            self.status = "done"

    def mark_partial(self, result: SearchRunResult) -> None:
        with self._lock:
            self.partial_result = result

    def mark_error(self, message: str) -> None:
        with self._lock:
            self.error_message = message
            self.status = "error"

    def request_stop(self) -> None:
        self.stop_event.set()
        with self._lock:
            if self.status == "running":
                self.status = "stopping"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "task_id": self.task_id,
                "profile_id": self.profile_id,
                "search_input": self.search_input,
                "source_ids": self.source_ids,
                "status": self.status,
                "phase_code": self.phase_code,
                "phase_label": self.phase_label,
                "current_query": self.current_query,
                "current_count": self.current_count,
                "total_count": self.total_count,
                "best_count": self.best_count,
                "last_source": self.last_source,
                "partial_result": self.partial_result,
                "result": self.result,
                "error_message": self.error_message,
                "elapsed": time.monotonic() - self.created_at,
            }

    def _record_attempt_count(self, found_count: int) -> None:
        if self._attempt_index < 0:
            self._attempt_index = 0
        self._attempt_counts[self._attempt_index] = max(
            self._attempt_counts.get(self._attempt_index, 0),
            found_count,
        )


_search_tasks: dict[str, _SearchTask] = {}
_tasks_lock = threading.Lock()


def _register_task(task: _SearchTask) -> None:
    with _tasks_lock:
        _search_tasks[task.task_id] = task
        stale = [k for k, v in _search_tasks.items()
                 if time.monotonic() - v.created_at > _TASK_MAX_AGE_SECONDS]
        for k in stale:
            del _search_tasks[k]


def _get_task(task_id: str) -> _SearchTask | None:
    with _tasks_lock:
        return _search_tasks.get(task_id)

router = APIRouter(tags=["web-jobs"])

SEARCH_PAGE_SIZE = 8
ALL_ENABLED_SOURCE_VALUE = "__enabled__"
JOBS_PAGE_SUBTITLE = "Детерминированный поиск с фильтрами, скорингом и понятной русской выдачей."
SEARCH_MODE_REMOTE = "remote_worldwide"
SEARCH_MODE_GERMANY = "germany_local"

def _get_client_ip(request: Request) -> str | None:
    """Extract the real client IP for passing to source adapters as user_ip.

    Local-first: uses the direct TCP connection address only.
    X-Forwarded-For is ignored — there is no trusted reverse proxy in this app.
    """
    client = request.client
    if client is not None:
        return client.host
    return None


def _location_to_de(location: str) -> str:
    return normalize_location(location) or location.strip()


def _role_to_query(role: str) -> str:
    return normalize_query_from_roles([role]) or role.strip()


def _profile_query_prefill(profile: object) -> str:
    search_query_de = getattr(profile, "search_query_de", None)
    if search_query_de:
        return search_query_de
    desired_roles = getattr(profile, "desired_roles", None)
    if desired_roles:
        return _role_to_query(desired_roles[0])
    return ""


def _profile_location_prefill(profile: object) -> str:
    preferred_locations = getattr(profile, "preferred_locations", None)
    if is_remote_worldwide_location(preferred_locations):
        return "remote"
    search_location_de = getattr(profile, "search_location_de", None)
    if search_location_de:
        return search_location_de
    if preferred_locations:
        return _location_to_de(preferred_locations[0])
    return ""


def _profile_search_mode_prefill(profile: object) -> str:
    preferred_locations = getattr(profile, "preferred_locations", None)
    search_location_de = (getattr(profile, "search_location_de", None) or "").strip().casefold()
    if is_remote_worldwide_location(preferred_locations) or search_location_de == "remote":
        return SEARCH_MODE_REMOTE
    return SEARCH_MODE_GERMANY


def get_search_history_service() -> SearchHistoryService:
    return SearchHistoryService()


def _build_source_options(source_descriptors: tuple[SourceAdapterDescriptor, ...]) -> tuple[dict[str, object], ...]:
    selectable_sources = tuple(
        source for source in source_descriptors
        if source.enabled and source.status_kind != "error"
    )
    options: list[dict[str, object]] = [
        {
            "value": ALL_ENABLED_SOURCE_VALUE,
            "label": "Все готовые источники",
            "disabled": not selectable_sources,
        }
    ]
    for source in source_descriptors:
        disabled = not source.enabled or source.status_kind == "error"
        options.append(
            {
                "value": source.source_id,
                "label": source.display_name,
                "disabled": disabled,
            }
        )
    return tuple(options)


def _build_source_status_rows(
    source_descriptors: tuple[SourceAdapterDescriptor, ...],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for source in source_descriptors:
        rows.append(
            {
                "descriptor": source,
                "is_russian": source.source_id in RUSSIAN_LANGUAGE_SOURCE_IDS,
                "mode_scope": "remote_worldwide" if source.global_remote else "germany_local",
            }
        )
    return tuple(rows)


def _build_profile_options(
    entries: list,
    selected_profile_id: int | None,
) -> tuple[dict[str, object], ...]:
    options: list[dict[str, object]] = []
    for entry in entries:
        label = entry.profile.name
        if entry.is_default:
            label = f"{label} (по умолчанию)"
        options.append(
            {
                "value": str(entry.profile.id),
                "label": label,
                "selected": entry.profile.id == selected_profile_id,
            }
        )
    return tuple(options)


def _default_form_values() -> dict[str, str]:
    return {
        "search_mode": SEARCH_MODE_GERMANY,
        "source": ALL_ENABLED_SOURCE_VALUE,
        "source_scope": SOURCE_SCOPE_WESTERN,
        "query": "",
        "location": "",
        "radius_km": "25",
    }


def _extract_form_values(form_data: Mapping[str, object]) -> dict[str, str]:
    values = _default_form_values()
    for field_name in values:
        raw_value = form_data.get(field_name)
        if raw_value is None:
            continue
        values[field_name] = str(raw_value).strip()
    if values["search_mode"] not in {SEARCH_MODE_REMOTE, SEARCH_MODE_GERMANY}:
        values["search_mode"] = SEARCH_MODE_GERMANY
    if values["source_scope"] not in {SOURCE_SCOPE_WESTERN, SOURCE_SCOPE_RUSSIAN}:
        values["source_scope"] = SOURCE_SCOPE_WESTERN
    if values["source_scope"] == SOURCE_SCOPE_RUSSIAN:
        values["search_mode"] = SEARCH_MODE_REMOTE
        values["location"] = "remote"
    if values["search_mode"] == SEARCH_MODE_REMOTE:
        values["location"] = values["location"] or "remote"
    elif values["location"].strip().lower() in {"remote", "worldwide remote", "remote worldwide", "global remote"}:
        values["location"] = "Deutschland"
    return values


def _parse_radius_km(raw_value: str) -> int | None:
    if not raw_value:
        return None
    try:
        radius = int(raw_value)
    except ValueError:
        return None
    if radius < 0:
        return None
    return radius


def _resolve_source_ids(form_values: Mapping[str, str], search_service: SearchService) -> tuple[str, ...]:
    if form_values["source_scope"] == SOURCE_SCOPE_RUSSIAN:
        return search_service.resolve_source_ids_for_search(source_scope=SOURCE_SCOPE_RUSSIAN)
    if form_values["source"] == ALL_ENABLED_SOURCE_VALUE:
        return search_service.resolve_source_ids_for_search(
            search_mode=form_values["search_mode"],
            source_scope=form_values["source_scope"],
        )
    return (form_values["source"],)


def _parse_profile_id(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    profile_id: int | None = None,
    search_service: SearchService = Depends(get_search_service),
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> HTMLResponse:
    meta = PAGE_META["jobs"]
    source_descriptors = search_service.list_sources()
    catalog_service.backfill_search_fields(db)
    profile_entries = catalog_service.list_profiles(db)

    # Resolve default if no explicit selection
    effective_profile_id = profile_id
    if effective_profile_id is None:
        effective_profile_id = catalog_service.resolve_default_profile_id(db)

    profile_options = _build_profile_options(profile_entries, selected_profile_id=effective_profile_id)

    # Автозаполняем форму из выбранного профиля
    form_values = _default_form_values()
    if effective_profile_id is not None:
        for entry in profile_entries:
            if entry.profile.id == effective_profile_id:
                profile = entry.profile
                form_values["query"] = _profile_query_prefill(profile)
                form_values["location"] = _profile_location_prefill(profile)
                form_values["search_mode"] = _profile_search_mode_prefill(profile)
                break

    return render_page(
        request,
        "pages/jobs.html",
        page_key="jobs",
        page_title=meta["title"],
        page_subtitle=JOBS_PAGE_SUBTITLE,
        extra_context={
            "source_descriptors": source_descriptors,
            "source_status_rows": _build_source_status_rows(source_descriptors),
            "source_options": _build_source_options(source_descriptors),
            "form_values": form_values,
            "search_result": None,
            "form_error": None,
            "profile_options": profile_options,
            "selected_profile_id": effective_profile_id,
        },
    )


@router.get("/jobs/profile-context", response_class=HTMLResponse)
def jobs_profile_context(
    request: Request,
    profile_id: int | None = None,
    search_service: SearchService = Depends(get_search_service),
) -> HTMLResponse:
    meta = PAGE_META["jobs"]
    return render_partial(
        request,
        "jobs/partials/profile_context.html",
        page_key="jobs",
        page_title=meta["title"],
        page_subtitle=JOBS_PAGE_SUBTITLE,
        extra_context={
            "profile_context": search_service.get_profile_context(profile_id=profile_id),
        },
    )


@router.get("/jobs/profile-autofill", response_class=HTMLResponse)
def jobs_profile_autofill(
    request: Request,
    profile_id: int | None = None,
    search_service: SearchService = Depends(get_search_service),
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> HTMLResponse:
    meta = PAGE_META["jobs"]
    profile_context = search_service.get_profile_context(profile_id=profile_id)

    query_prefill = ""
    location_prefill = ""
    search_mode_prefill = SEARCH_MODE_GERMANY
    if profile_id is not None:
        profile = catalog_service.get_profile(db, profile_id=profile_id)
        if profile is not None:
            query_prefill = _profile_query_prefill(profile)
            location_prefill = _profile_location_prefill(profile)
            search_mode_prefill = _profile_search_mode_prefill(profile)

    return render_partial(
        request,
        "jobs/partials/profile_autofill.html",
        page_key="jobs",
        page_title=meta["title"],
        page_subtitle=JOBS_PAGE_SUBTITLE,
        extra_context={
            "profile_context": profile_context,
            "query_prefill": query_prefill,
            "location_prefill": location_prefill,
            "search_mode_prefill": search_mode_prefill,
        },
    )


def _render_progress(request: Request, task: _SearchTask) -> HTMLResponse:
    snap = task.snapshot()
    meta = PAGE_META["jobs"]
    snail_animation_duration = 16 if snap["phase_code"] == "postprocess" else 18
    snail_animation_offset = snap["elapsed"] % snail_animation_duration
    return render_partial(
        request,
        "jobs/partials/search_progress.html",
        page_key="jobs",
        page_title=meta["title"],
        page_subtitle=JOBS_PAGE_SUBTITLE,
        extra_context={
            "task_id": snap["task_id"],
            "phase_code": snap["phase_code"],
            "phase_label": snap["phase_label"],
            "current_query": snap["current_query"],
            "current_count": snap["current_count"],
            "total_count": snap["total_count"],
            "best_count": snap["best_count"],
            "last_source": snap["last_source"],
            "partial_result": snap["partial_result"],
            "stopping": snap["status"] == "stopping",
            "show_stop_button": snap["elapsed"] >= _STOP_BUTTON_DELAY_SECONDS,
            "snail_animation_offset": f"{snail_animation_offset:.2f}",
        },
    )


def _render_results(request: Request, result: SearchRunResult | None, profile_id: int | None,
                    form_error: str | None = None, task_id: str | None = None) -> HTMLResponse:
    meta = PAGE_META["jobs"]
    return render_partial(
        request,
        "jobs/partials/search_results.html",
        page_key="jobs",
        page_title=meta["title"],
        page_subtitle=JOBS_PAGE_SUBTITLE,
        extra_context={
            "search_result": result,
            "form_error": form_error,
            "profile_id": profile_id,
            # Present only for completed background searches — enables the PDF export button.
            "export_task_id": task_id if (result is not None and result.has_results) else None,
        },
    )


def _build_feedback_memory(db: Session, profile_id: int | None) -> ProfileFeedbackMemory | None:
    if profile_id is None:
        return None
    try:
        return RelevanceMemoryService().build_profile_memory(db, profile_id=profile_id)
    except Exception:
        logger.warning("feedback_memory_build_failed profile_id=%s", profile_id)
        return None


@router.post("/jobs/search/start", response_class=HTMLResponse)
async def jobs_search_start(
    request: Request,
    search_service: SearchService = Depends(get_search_service),
    search_history_service: SearchHistoryService = Depends(get_search_history_service),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Запускает поиск в фоне и немедленно возвращает панель прогресса."""
    form_data: FormData = await read_form_data(request)
    form_values = _extract_form_values(form_data)
    profile_id = _parse_profile_id(form_data.get("profile_id"))

    if not form_values["query"]:
        return _render_results(
            request, None, profile_id,
            form_error="Введите запрос, чтобы получить список подходящих вакансий.",
        )

    search_input = SourceSearchInput(
        query=form_values["query"],
        location=form_values["location"] or None,
        radius_km=_parse_radius_km(form_values["radius_km"]),
        search_mode=form_values["search_mode"],  # type: ignore[arg-type]
        page=1,
        page_size=SEARCH_PAGE_SIZE,
        user_ip=_get_client_ip(request),
    )
    source_ids = _resolve_source_ids(form_values, search_service)

    # Pre-build feedback memory synchronously (pure data object — safe for background thread)
    feedback_memory = _build_feedback_memory(db, profile_id)

    task = _SearchTask(
        task_id=uuid.uuid4().hex[:8],
        profile_id=profile_id,
        search_input=search_input,
        source_ids=tuple(source_ids),
    )
    _register_task(task)

    def _run_in_background() -> None:
        try:
            def _on_progress(phase: str, found_count: int, source_name: str | None, query_used: str | None) -> None:
                if phase == "attempt":
                    task.on_attempt_started(query_used)
                elif phase == "postprocess":
                    task.on_postprocess(found_count, query=query_used)
                else:
                    task.on_source_done(found_count, source_name or "", query=query_used)

            result = search_service.orchestrated_search(
                search_input=search_input,
                source_ids=source_ids,
                profile_id=profile_id,
                progress_callback=_on_progress,
                partial_result_callback=task.mark_partial,
                stop_event=task.stop_event,
                feedback_memory=feedback_memory,
            )
            search_history_service.record_search_result(result=result, profile_id=profile_id)
            task.mark_done(result)
        except Exception:
            logger.exception("jobs_search_background_failed task_id=%s", task.task_id)
            task.mark_error("Не удалось выполнить поиск. Проверьте доступность источников.")

    threading.Thread(target=_run_in_background, daemon=True).start()
    return _render_progress(request, task)


@router.get("/jobs/search/progress/{task_id}", response_class=HTMLResponse)
def jobs_search_progress(
    task_id: str,
    request: Request,
) -> HTMLResponse:
    """Polling-эндпоинт: возвращает прогресс или готовые результаты."""
    task = _get_task(task_id)
    if task is None:
        return _render_results(request, None, None, form_error="Задача поиска не найдена.")

    snap = task.snapshot()
    if snap["status"] == "done":
        return _render_results(request, snap["result"], snap["profile_id"], task_id=snap["task_id"])
    if snap["status"] == "error":
        return _render_results(request, None, snap["profile_id"], form_error=snap["error_message"])

    return _render_progress(request, task)


@router.get("/jobs/export/{task_id}.json")
def jobs_export_json(task_id: str, mode: str = EXPORT_MODE_DIAGNOSTICS) -> Response:
    """JSON-выгрузка завершённого поиска — для анализа выдачи.

    По умолчанию компактный диагностический режим: полная выгрузка на реальном прогоне
    весит ~112k токенов и в окно модели уже не помещается. `?mode=full` — со всеми текстами.

    Объявлен ДО "/jobs/export/{task_id}": иначе ".json" попал бы в сам task_id.
    """
    if mode not in EXPORT_MODES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Неизвестный режим {mode!r}. Допустимы: {', '.join(EXPORT_MODES)}."},
        )

    task = _get_task(task_id)
    snap = task.snapshot() if task is not None else None
    if snap is None or snap["status"] != "done" or snap["result"] is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Завершённый поиск с таким task_id не найден."},
        )

    generated_at = utc_now()
    payload = build_search_export(
        snap["result"],
        task_id=task_id,
        generated_at=generated_at,
        search_input=snap["search_input"],
        source_ids_requested=snap["source_ids"],
        mode=mode,
    )
    filename = build_export_filename(task_id, generated_at, mode=mode)
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs/export/{task_id}", response_class=HTMLResponse)
def jobs_export(task_id: str, request: Request) -> HTMLResponse:
    """Print-optimized export of a completed search run (browser "Сохранить как PDF")."""
    task = _get_task(task_id)
    snap = task.snapshot() if task is not None else None
    result: SearchRunResult | None = snap["result"] if snap and snap["status"] == "done" else None

    meta = PAGE_META["jobs"]
    return templates.TemplateResponse(
        request=request,
        name="jobs/export.html",
        context={
            "page_title": meta["title"],
            "search_result": result,
            "generated_at": utc_now(),
        },
    )


@router.post("/jobs/search/stop/{task_id}", response_class=HTMLResponse)
def jobs_search_stop(
    task_id: str,
    request: Request,
) -> HTMLResponse:
    """Устанавливает флаг остановки; поиск завершится после текущего источника."""
    task = _get_task(task_id)
    if task is None:
        return _render_results(request, None, None, form_error="Задача поиска не найдена.")

    task.request_stop()
    return _render_progress(request, task)


@router.post("/jobs/search-results", response_class=HTMLResponse)
async def jobs_search_results(
    request: Request,
    search_service: SearchService = Depends(get_search_service),
    search_history_service: SearchHistoryService = Depends(get_search_history_service),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    meta = PAGE_META["jobs"]
    form_data: FormData = await read_form_data(request)
    form_values = _extract_form_values(form_data)
    profile_id = _parse_profile_id(form_data.get("profile_id"))
    form_error: str | None = None
    search_result = None

    if not form_values["query"]:
        form_error = "Введите запрос, чтобы получить список подходящих вакансий."
    else:
        search_input = SourceSearchInput(
            query=form_values["query"],
            location=form_values["location"] or None,
            radius_km=_parse_radius_km(form_values["radius_km"]),
            search_mode=form_values["search_mode"],  # type: ignore[arg-type]
            page=1,
            page_size=SEARCH_PAGE_SIZE,
            user_ip=_get_client_ip(request),
        )
        feedback_memory = _build_feedback_memory(db, profile_id)
        try:
            # Долгий синхронный поиск (сеть + retry) — уводим в worker-поток,
            # иначе event loop блокируется и сервер перестаёт отвечать.
            search_result = await run_in_threadpool(
                search_service.orchestrated_search,
                search_input=search_input,
                source_ids=_resolve_source_ids(form_values, search_service),
                profile_id=profile_id,
                feedback_memory=feedback_memory,
            )
        except Exception:
            logger.exception("jobs_search_failed")
            form_error = (
                "Не удалось выполнить поиск. Проверьте доступность источников и базы данных, затем повторите попытку."
            )
        else:
            if search_result.profile.profile_source == "degraded":
                logger.warning(
                    "jobs_search_degraded_mode query=%s location=%s source=%s profile_id=%s",
                    form_values["query"],
                    form_values["location"] or "-",
                    form_values["source"],
                    profile_id,
                )
            elif search_result.profile.profile_source == "fallback":
                logger.info(
                    "jobs_search_fallback_mode query=%s location=%s source=%s profile_id=%s",
                    form_values["query"],
                    form_values["location"] or "-",
                    form_values["source"],
                    profile_id,
                )
            search_history_service.record_search_result(result=search_result, profile_id=profile_id)

    return render_partial(
        request,
        "jobs/partials/search_results.html",
        page_key="jobs",
        page_title=meta["title"],
        page_subtitle=JOBS_PAGE_SUBTITLE,
        extra_context={
            "search_result": search_result,
            "form_error": form_error,
            "profile_id": profile_id,
        },
    )
