from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape
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

_HTML_RE = re.compile(r"<[^>]+>")


class GreenhouseAdapter(BaseSourceAdapter):
    source_id = "greenhouse"
    display_name = "Greenhouse"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_greenhouse_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        tokens = _split_csv(self.settings.source_greenhouse_board_tokens)
        if not enabled:
            status_label, status_kind, detail = "Отключен", "disabled", "SOURCE_GREENHOUSE_ENABLED=false."
        elif not tokens:
            status_label, status_kind, detail = "Отключен", "disabled", "Нет SOURCE_GREENHOUSE_BOARD_TOKENS."
        else:
            status_label, status_kind, detail = "Готов", "success", f"Job Board API: {len(tokens)} boards."
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled and bool(tokens),
            status_label=status_label,
            status_kind=status_kind,  # type: ignore[arg-type]
            status_detail=detail,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        board_tokens = _split_csv(self.settings.source_greenhouse_board_tokens)
        if not board_tokens:
            return AdapterSearchResponse(
                source_id=self.source_id,
                source_name=self.display_name,
                records=(),
                total_count=0,
                page=search_input.page,
                page_size=search_input.page_size,
                raw_payload={"boards": ()},
                warnings=("Greenhouse: SOURCE_GREENHOUSE_BOARD_TOKENS не задан.",),
            )

        records: list[SourceRecordPreview] = []
        warnings: list[str] = []
        raw_payloads: dict[str, Any] = {}
        for board_token in board_tokens:
            endpoint = f"{self.settings.source_greenhouse_base_url.rstrip('/')}/{board_token}/jobs"
            payload = self._fetch_payload(endpoint, params={"content": "true"})
            raw_payloads[board_token] = payload
            raw_jobs = payload.get("jobs", [])
            if not isinstance(raw_jobs, list):
                warnings.append(f"Greenhouse {board_token}: ответ не содержит список jobs.")
                continue
            for raw_job in raw_jobs:
                if not isinstance(raw_job, Mapping):
                    continue
                if not _matches_query(raw_job, search_input.query):
                    continue
                if not _matches_mode(raw_job, search_input.search_mode):
                    continue
                record = _parse_record(self.source_id, self.display_name, board_token, raw_job)
                if record is not None:
                    records.append(record)

        page = max(1, search_input.page)
        page_size = search_input.page_size
        start = (page - 1) * page_size
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records[start:start + page_size]),
            total_count=len(records),
            page=page,
            page_size=page_size,
            raw_payload={"boards": raw_payloads},
            warnings=tuple(warnings),
        )

    def _fetch_payload(self, endpoint: str, *, params: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            http_response = self.http_transport.get_json(
                endpoint,
                params=params,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Greenhouse. {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Greenhouse вернул некорректный JSON: {exc.message}",
            ) from exc
        if not isinstance(http_response.payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Greenhouse ответ имеет неожиданный формат верхнего уровня.",
            )
        return http_response.payload


def _parse_record(source_id: str, source_name: str, board_token: str, raw_job: Mapping[str, Any]) -> SourceRecordPreview | None:
    job_id = _to_text(raw_job.get("id"))
    if job_id is None:
        return None
    location_block = raw_job.get("location")
    location = _to_text(location_block.get("name")) if isinstance(location_block, Mapping) else None
    raw_payload = dict(raw_job)
    raw_payload["description"] = _plain_text(raw_job.get("content"))
    raw_payload["company_board"] = board_token
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=f"{board_token}:{job_id}",
        source_reference=job_id,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=board_token,
        location=location,
        posted_at=_to_text(raw_job.get("updated_at")),
        detail_url=_to_text(raw_job.get("absolute_url")),
        raw_payload=raw_payload,
    )


def _matches_query(raw_job: Mapping[str, Any], query: str) -> bool:
    query_parts = tuple(part for part in query.casefold().split() if part)
    if not query_parts:
        return True
    haystack = " ".join(
        filter(None, [_to_text(raw_job.get("title")), _plain_text(raw_job.get("content"))])
    ).casefold()
    return any(part in haystack for part in query_parts)


def _matches_mode(raw_job: Mapping[str, Any], search_mode: str) -> bool:
    text = " ".join(filter(None, [_location_text(raw_job), _plain_text(raw_job.get("content"))])).casefold()
    if search_mode == "remote_worldwide":
        return any(term in text for term in ("remote", "worldwide", "anywhere", "europe"))
    return any(term in text for term in ("germany", "deutschland", "berlin", "hamburg", "munich", "münchen"))


def _location_text(raw_job: Mapping[str, Any]) -> str | None:
    location_block = raw_job.get("location")
    return _to_text(location_block.get("name")) if isinstance(location_block, Mapping) else None


def _plain_text(value: Any) -> str | None:
    text = _to_text(value)
    if text is None:
        return None
    return unescape(_HTML_RE.sub(" ", text))


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
