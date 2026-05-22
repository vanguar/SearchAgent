from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import Settings
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError, AdapterResponseError, HttpDecodeError, HttpTransportError
from app.services.source_adapters.http import HttpJsonTransport, UrllibHttpJsonTransport
from app.services.source_adapters.models import AdapterSearchResponse, SourceAdapterDescriptor, SourceRecordPreview, SourceSearchInput


class RemoteJobsOrgAdapter(BaseSourceAdapter):
    source_id = "remotejobs"
    display_name = "RemoteJobs.org"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_remotejobs_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail="Бесплатный JSON API remote-вакансий." if enabled else "SOURCE_REMOTEJOBS_ENABLED=false.",
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        limit = min(max(search_input.page_size, 1), 50)
        offset = max(0, search_input.page - 1) * limit
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if search_input.query:
            params["q"] = search_input.query

        try:
            http_response = self.http_transport.get_json(
                self.settings.source_remotejobs_base_url,
                params=params,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от RemoteJobs.org.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"RemoteJobs.org вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="RemoteJobs.org ответ имеет неожиданный формат верхнего уровня.",
            )
        raw_jobs = payload.get("data", [])
        if not isinstance(raw_jobs, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="RemoteJobs.org ответ не содержит список data.",
            )

        records = tuple(
            record
            for raw_job in raw_jobs
            if isinstance(raw_job, Mapping)
            if (record := _parse_record(self.source_id, self.display_name, raw_job)) is not None
        )
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=records,
            total_count=_pagination_total(payload),
            page=search_input.page,
            page_size=limit,
            raw_payload=dict(payload),
            warnings=("RemoteJobs.org просит показывать attribution Powered by RemoteJobs.org.",),
        )


def _parse_record(source_id: str, source_name: str, raw_job: Mapping[str, Any]) -> SourceRecordPreview | None:
    external_id = _to_text(raw_job.get("id")) or _to_text(raw_job.get("url"))
    if external_id is None:
        return None
    company = raw_job.get("company")
    company_name = _to_text(company.get("name")) if isinstance(company, Mapping) else None
    raw_payload = dict(raw_job)
    salary_text = _to_text(raw_job.get("salary_text"))
    if salary_text:
        raw_payload["salary"] = salary_text
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=company_name,
        location=_to_text(raw_job.get("location")) or "Remote",
        posted_at=_to_text(raw_job.get("posted_at")),
        detail_url=_to_text(raw_job.get("apply_url")) or _to_text(raw_job.get("url")),
        raw_payload=raw_payload,
    )


def _pagination_total(payload: Mapping[str, Any]) -> int | None:
    pagination = payload.get("pagination")
    if isinstance(pagination, Mapping):
        total = pagination.get("total")
        try:
            return int(total) if total is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
