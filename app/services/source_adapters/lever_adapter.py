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


class LeverAdapter(BaseSourceAdapter):
    source_id = "lever"
    display_name = "Lever"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_lever_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        slugs = _split_csv(self.settings.source_lever_company_slugs)
        if not enabled:
            status_label, status_kind, detail = "Отключен", "disabled", "SOURCE_LEVER_ENABLED=false."
        elif not slugs:
            status_label, status_kind, detail = "Отключен", "disabled", "Нет SOURCE_LEVER_COMPANY_SLUGS."
        else:
            status_label, status_kind, detail = "Готов", "success", f"Postings API: {len(slugs)} companies."
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled and bool(slugs),
            status_label=status_label,
            status_kind=status_kind,  # type: ignore[arg-type]
            status_detail=detail,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        slugs = _split_csv(self.settings.source_lever_company_slugs)
        if not slugs:
            return AdapterSearchResponse(
                source_id=self.source_id,
                source_name=self.display_name,
                records=(),
                total_count=0,
                page=search_input.page,
                page_size=search_input.page_size,
                raw_payload={"companies": ()},
                warnings=("Lever: SOURCE_LEVER_COMPANY_SLUGS не задан.",),
            )

        records: list[SourceRecordPreview] = []
        raw_payloads: dict[str, Any] = {}
        for slug in slugs:
            endpoint = f"{self.settings.source_lever_base_url.rstrip('/')}/{slug}"
            payload = self._fetch_payload(endpoint, params={"mode": "json"})
            raw_payloads[slug] = payload
            for raw_job in payload:
                if not isinstance(raw_job, Mapping):
                    continue
                if not _matches_query(raw_job, search_input.query):
                    continue
                if not _matches_mode(raw_job, search_input.search_mode):
                    continue
                record = _parse_record(self.source_id, self.display_name, slug, raw_job)
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
            raw_payload={"companies": raw_payloads},
            warnings=(),
        )

    def _fetch_payload(self, endpoint: str, *, params: Mapping[str, Any]) -> list[Any]:
        try:
            http_response = self.http_transport.get_json(
                endpoint,
                params=params,
                headers={"Accept": "application/json"},
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Lever. {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Lever вернул некорректный JSON: {exc.message}",
            ) from exc
        if not isinstance(http_response.payload, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Lever ответ имеет неожиданный формат верхнего уровня.",
            )
        return http_response.payload


def _parse_record(source_id: str, source_name: str, company_slug: str, raw_job: Mapping[str, Any]) -> SourceRecordPreview | None:
    job_id = _to_text(raw_job.get("id"))
    if job_id is None:
        return None
    categories = raw_job.get("categories")
    location = _to_text(categories.get("location")) if isinstance(categories, Mapping) else None
    raw_payload = dict(raw_job)
    raw_payload["description"] = _to_text(raw_job.get("descriptionPlain")) or _to_text(raw_job.get("description"))
    raw_payload["company_slug"] = company_slug
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=f"{company_slug}:{job_id}",
        source_reference=job_id,
        title=_to_text(raw_job.get("text")) or "Без названия",
        company=company_slug,
        location=location,
        posted_at=_to_text(raw_job.get("createdAt")),
        detail_url=_to_text(raw_job.get("hostedUrl")) or _to_text(raw_job.get("applyUrl")),
        raw_payload=raw_payload,
    )


def _matches_query(raw_job: Mapping[str, Any], query: str) -> bool:
    query_parts = tuple(part for part in query.casefold().split() if part)
    if not query_parts:
        return True
    haystack = " ".join(
        filter(None, [_to_text(raw_job.get("text")), _to_text(raw_job.get("descriptionPlain")), _to_text(raw_job.get("description"))])
    ).casefold()
    return any(part in haystack for part in query_parts)


def _matches_mode(raw_job: Mapping[str, Any], search_mode: str) -> bool:
    categories = raw_job.get("categories")
    location = _to_text(categories.get("location")) if isinstance(categories, Mapping) else None
    text = " ".join(filter(None, [location, _to_text(raw_job.get("descriptionPlain")), _to_text(raw_job.get("text"))])).casefold()
    if search_mode == "remote_worldwide":
        return any(term in text for term in ("remote", "worldwide", "anywhere", "europe"))
    return any(term in text for term in ("germany", "deutschland", "berlin", "hamburg", "munich", "münchen"))


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
