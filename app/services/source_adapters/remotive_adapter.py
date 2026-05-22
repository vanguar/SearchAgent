from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import Settings
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import (
    AdapterRequestError,
    AdapterResponseError,
    HttpDecodeError,
    HttpTransportError,
)
from app.services.source_adapters.http import HttpJsonTransport, UrllibHttpJsonTransport
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)

# Только явно Германия/Европа/DACH/EMEA. Неизвестная локация → исключаем.
_GERMANY_COMPATIBLE_TERMS = frozenset(
    {
        "germany",
        "deutschland",
        "europe",
        "european",
        "eu",
        "emea",
        "dach",
    }
)

_REMOTE_WORLDWIDE_TERMS = frozenset({"worldwide", "anywhere", "global", "remote"})


def _is_germany_compatible(location_str: str | None) -> bool:
    """Вернуть True только если локация явно совместима с Германией."""
    if not location_str:
        # Пустая локация — неизвестно, исключаем
        return False
    loc_lower = location_str.lower()
    return any(term in loc_lower for term in _GERMANY_COMPATIBLE_TERMS)


def _is_remote_worldwide_search(search_input: SourceSearchInput) -> bool:
    return search_input.search_mode == "remote_worldwide"


def _is_remote_compatible(location_str: str | None) -> bool:
    if not location_str:
        return False
    loc_lower = location_str.lower()
    return any(term in loc_lower for term in _GERMANY_COMPATIBLE_TERMS | _REMOTE_WORLDWIDE_TERMS)


class RemotiveAdapter(BaseSourceAdapter):
    source_id = "remotive"
    display_name = "Remotive"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_remotive_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail="Глобальный remote-источник." if enabled else "SOURCE_REMOTIVE_ENABLED=false.",
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        # Remotive API не поддерживает пагинацию: для remote_worldwide берём все совпадения API.
        params: dict[str, Any] = {
            "search": search_input.query or None,
        }

        # Cloudflare на remotive.com блокирует стандартный urllib User-Agent
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

        try:
            http_response = self.http_transport.get_json(
                self.settings.source_remotive_base_url,
                params=params,
                headers=headers,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Remotive.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Remotive вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Remotive ответ имеет неожиданный формат верхнего уровня.",
            )

        raw_jobs = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Remotive ответ не содержит список jobs.",
            )

        records: list[SourceRecordPreview] = []
        skipped_no_id = 0
        filtered_non_germany = 0
        remote_worldwide_search = _is_remote_worldwide_search(search_input)

        for raw_job in raw_jobs:
            if not isinstance(raw_job, Mapping):
                skipped_no_id += 1
                continue

            location_field = _to_text(raw_job.get("candidate_required_location"))
            location_compatible = True if remote_worldwide_search else _is_germany_compatible(location_field)
            if not location_compatible:
                filtered_non_germany += 1
                continue

            record = _parse_record(self.source_id, self.display_name, raw_job)
            if record is None:
                skipped_no_id += 1
                continue
            records.append(record)

        page = search_input.page
        page_size = search_input.page_size
        if remote_worldwide_search:
            paged_records = records
        else:
            # Применяем ручную пагинацию поверх отфильтрованных результатов
            start = (page - 1) * page_size
            end = start + page_size
            paged_records = records[start:end]

        warnings: list[str] = []
        if filtered_non_germany:
            warnings.append(f"Remotive: отфильтровано {filtered_non_germany} вакансий вне Германии/Европы.")
        if skipped_no_id:
            warnings.append(f"Remotive: пропущено {skipped_no_id} записей без стабильного ID.")

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(paged_records),
            total_count=len(records),
            page=page,
            page_size=page_size,
            raw_payload=dict(payload),
            warnings=tuple(warnings),
        )


def _parse_record(
    source_id: str,
    source_name: str,
    raw_job: Mapping[str, Any],
) -> SourceRecordPreview | None:
    job_id = raw_job.get("id")
    if job_id is None:
        return None
    external_id = str(job_id)

    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=_to_text(raw_job.get("company_name")),
        location=_to_text(raw_job.get("candidate_required_location")),
        posted_at=_to_text(raw_job.get("publication_date")),
        detail_url=_to_text(raw_job.get("url")),
        raw_payload=dict(raw_job),
    )


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
