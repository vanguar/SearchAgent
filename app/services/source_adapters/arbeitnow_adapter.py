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

_REMOTE_TERMS = ("remote", "worldwide", "anywhere", "europe")


class ArbeitnowAdapter(BaseSourceAdapter):
    source_id = "arbeitnow"
    display_name = "Arbeitnow"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_arbeitnow_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail="Публичный Europe/remote JSON API." if enabled else "SOURCE_ARBEITNOW_ENABLED=false.",
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        try:
            http_response = self.http_transport.get_json(
                self.settings.source_arbeitnow_base_url,
                params={"page": max(1, search_input.page)},
                # arbeitnow.com sits behind Cloudflare, which 429-challenges plain bot
                # User-Agents; send a browser-like UA (same approach as the Remotive adapter).
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                },
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Arbeitnow.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Arbeitnow вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        raw_jobs = _extract_jobs(payload)
        records: list[SourceRecordPreview] = []
        skipped = 0
        for raw_job in raw_jobs:
            if not isinstance(raw_job, Mapping):
                skipped += 1
                continue
            if not _matches_query(raw_job, search_input.query):
                continue
            if not _matches_mode(raw_job, search_input.search_mode):
                continue
            record = _parse_record(self.source_id, self.display_name, raw_job)
            if record is None:
                skipped += 1
                continue
            records.append(record)

        page = max(1, search_input.page)
        page_size = search_input.page_size
        paged_records = tuple(records[:page_size])
        warnings = (f"Arbeitnow: пропущено {skipped} записей без стабильного ID.",) if skipped else ()
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=paged_records,
            total_count=len(records),
            page=page,
            page_size=page_size,
            raw_payload=payload,
            warnings=warnings,
        )


def _extract_jobs(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        data = payload.get("data", payload.get("jobs", []))
        if isinstance(data, list):
            return data
    raise AdapterResponseError(
        source_id=ArbeitnowAdapter.source_id,
        source_name=ArbeitnowAdapter.display_name,
        message="Arbeitnow ответ не содержит список вакансий.",
    )


def _parse_record(source_id: str, source_name: str, raw_job: Mapping[str, Any]) -> SourceRecordPreview | None:
    external_id = _to_text(raw_job.get("slug")) or _to_text(raw_job.get("id")) or _to_text(raw_job.get("url"))
    if external_id is None:
        return None
    raw_payload = dict(raw_job)
    tags = raw_job.get("tags")
    if isinstance(tags, list):
        raw_payload["description"] = " ".join(str(tag) for tag in tags if tag)
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=_to_text(raw_job.get("company_name")),
        location=_to_text(raw_job.get("location")) or ("Remote" if _truthy(raw_job.get("remote")) else None),
        posted_at=_to_text(raw_job.get("created_at")) or _to_text(raw_job.get("date")),
        detail_url=_to_text(raw_job.get("url")),
        raw_payload=raw_payload,
    )


def _matches_query(raw_job: Mapping[str, Any], query: str) -> bool:
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return True
    haystack = " ".join(
        filter(
            None,
            [
                _to_text(raw_job.get("title")),
                _to_text(raw_job.get("company_name")),
                _to_text(raw_job.get("description")),
                " ".join(str(tag) for tag in raw_job.get("tags", []) if tag) if isinstance(raw_job.get("tags"), list) else None,
            ],
        )
    ).casefold()
    return any(part in haystack for part in normalized_query.split())


def _matches_mode(raw_job: Mapping[str, Any], search_mode: str) -> bool:
    location = (_to_text(raw_job.get("location")) or "").casefold()
    remote = _truthy(raw_job.get("remote"))
    if search_mode == "remote_worldwide":
        return remote or any(term in location for term in _REMOTE_TERMS)
    return True


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().casefold() in {"1", "true", "yes", "remote"}


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
