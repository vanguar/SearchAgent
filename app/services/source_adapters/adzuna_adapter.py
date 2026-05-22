from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.core.config import Settings
from app.core.logging import logger
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import (
    AdapterConfigurationError,
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


class AdzunaAdapter(BaseSourceAdapter):
    """Адаптер Adzuna Jobs API v1 — Германия (country=de).

    Документация: https://developer.adzuna.com/
    URL: GET {base_url}/{page}?app_id=...&app_key=...&what=...&where=...
    """

    source_id = "adzuna"
    display_name = "Adzuna"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_adzuna_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        if not enabled:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=False,
                status_label="Отключен",
                status_kind="disabled",
                status_detail="SOURCE_ADZUNA_ENABLED=false.",
            )
        if not self.settings.adzuna_app_id or not self.settings.adzuna_app_key:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=True,
                status_label="Не работает",
                status_kind="error",
                status_detail="Нет ADZUNA_APP_ID или ADZUNA_APP_KEY.",
            )
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=True,
            status_label="Готов",
            status_kind="success",
            status_detail="Работает через немецкий индекс Adzuna; не является worldwide-источником.",
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        if not self.settings.adzuna_app_id or not self.settings.adzuna_app_key:
            raise AdapterConfigurationError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=(
                    "Adzuna требует ADZUNA_APP_ID и ADZUNA_APP_KEY. "
                    "Ключи задайте в .env-файле."
                ),
            )

        # Adzuna строит URL как {base}/{page}
        page = max(1, search_input.page)
        endpoint = f"{self.settings.source_adzuna_base_url.rstrip('/')}/{page}"

        params: dict[str, Any] = {
            "app_id": self.settings.adzuna_app_id,
            "app_key": self.settings.adzuna_app_key,
            "results_per_page": search_input.page_size,
            "content-type": "application/json",
        }
        if search_input.query:
            params["what"] = search_input.query
        locations = _effective_locations(search_input)
        if search_input.radius_km is not None:
            params["distance"] = search_input.radius_km

        logger.info(
            "adzuna_search query=%r location=%r effective_locations=%s radius=%s page=%s",
            search_input.query, search_input.location, locations or None, search_input.radius_km, page,
        )

        payloads = tuple(
            self._fetch_payload(
                endpoint,
                params={**params, "where": location} if location else params,
            )
            for location in (locations or (None,))
        )

        records: list[SourceRecordPreview] = []
        seen_external_ids: set[str] = set()
        skipped = 0
        total_count = 0
        total_count_known = False
        for payload in payloads:
            payload_count = _to_int(payload.get("count"))
            if payload_count is not None:
                total_count += payload_count
                total_count_known = True
            for raw_job in _extract_results(payload):
                if not isinstance(raw_job, Mapping):
                    skipped += 1
                    continue
                record = _parse_record(self.source_id, self.display_name, raw_job)
                if record is None:
                    skipped += 1
                    continue
                if record.external_id in seen_external_ids:
                    continue
                seen_external_ids.add(record.external_id)
                records.append(record)

        warnings: list[str] = []
        if skipped:
            warnings.append(f"Adzuna: пропущено {skipped} записей без стабильного ID.")
        if len(payloads) > 1:
            warnings.append(f"Adzuna: выполнен отдельный поиск по {len(payloads)} городам.")

        raw_payload: dict[str, Any]
        if len(payloads) == 1:
            raw_payload = dict(payloads[0])
        else:
            raw_payload = {
                "count": total_count if total_count_known else None,
                "results": [record.raw_payload for record in records],
                "locations": locations,
                "responses": tuple(dict(payload) for payload in payloads),
            }

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            total_count=total_count if total_count_known else None,
            page=page,
            page_size=search_input.page_size,
            raw_payload=raw_payload,
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
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Adzuna.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Adzuna вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Adzuna ответ имеет неожиданный формат верхнего уровня.",
            )
        _extract_results(payload)
        return payload


def _parse_record(
    source_id: str,
    source_name: str,
    raw_job: Mapping[str, Any],
) -> SourceRecordPreview | None:
    job_id = raw_job.get("id")
    if job_id is None:
        return None
    external_id = str(job_id)

    company_block = raw_job.get("company")
    company: str | None = None
    if isinstance(company_block, Mapping):
        company = _to_text(company_block.get("display_name"))

    location_block = raw_job.get("location")
    location: str | None = None
    if isinstance(location_block, Mapping):
        location = _to_text(location_block.get("display_name"))

    # created — ISO 8601: "2026-04-15T12:00:00Z" → берём только дату
    posted_at: str | None = None
    created_raw = _to_text(raw_job.get("created"))
    if created_raw and "T" in created_raw:
        posted_at = created_raw.split("T")[0]
    else:
        posted_at = created_raw

    # Строим salary_text из salary_min/salary_max
    salary_text = _build_salary_text(raw_job)

    raw_payload: dict[str, Any] = dict(raw_job)
    if salary_text:
        raw_payload["salary"] = salary_text

    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=company,
        location=location,
        posted_at=posted_at,
        detail_url=_to_text(raw_job.get("redirect_url")),
        raw_payload=raw_payload,
    )


_LOCATION_SPLIT_RE = re.compile(r"[,;|/]+")
_COUNTRY_LOCATION_PARTS = frozenset({"de", "deutschland", "germany", "allemagne"})


def _effective_locations(search_input: SourceSearchInput) -> tuple[str, ...]:
    """Adzuna `where` accepts one place; split city lists into separate requests."""
    if search_input.search_mode == "remote_worldwide":
        return ()
    location = search_input.location
    if not location or not location.strip():
        return ()
    parts = tuple(part.strip() for part in _LOCATION_SPLIT_RE.split(location) if part.strip())
    city_parts = tuple(part for part in parts if part.casefold() not in _COUNTRY_LOCATION_PARTS)
    if len(city_parts) > 1:
        return city_parts
    return (location.strip(),)


def _extract_results(payload: Mapping[str, Any]) -> list[Any]:
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise AdapterResponseError(
            source_id=AdzunaAdapter.source_id,
            source_name=AdzunaAdapter.display_name,
            message="Adzuna ответ не содержит список results.",
        )
    return raw_results


def _build_salary_text(raw_job: Mapping[str, Any]) -> str | None:
    s_min = raw_job.get("salary_min")
    s_max = raw_job.get("salary_max")
    if s_min is not None and s_max is not None:
        return f"{int(s_min)}–{int(s_max)} EUR"
    if s_min is not None:
        return f"от {int(s_min)} EUR"
    if s_max is not None:
        return f"до {int(s_max)} EUR"
    return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
