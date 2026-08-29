from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from app.core.config import Settings
from app.core.logging import logger
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

_BA_PUBLIC_JOBSEARCH_URL = "https://www.arbeitsagentur.de/jobsuche/suche"
_BA_SEARCH_PATH = "/pc/v6/jobs"


class BAAdapter(BaseSourceAdapter):
    source_id = "ba"
    display_name = "BA (Bundesagentur fur Arbeit)"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_ba_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail=(
                "Работает через Bundesagentur fuer Arbeit; источник ограничен Германией."
                if enabled
                else "SOURCE_BA_ENABLED=false."
            ),
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        endpoint = f"{self.settings.source_ba_base_url.rstrip('/')}{_BA_SEARCH_PATH}"
        location = _effective_location(search_input)
        params: dict[str, Any] = {
            "was": search_input.query or None,
            "page": search_input.page,
            "size": search_input.page_size,
        }
        if location:
            params["wo"] = location
            if search_input.radius_km is not None:
                params["umkreis"] = search_input.radius_km
        headers = {
            "X-API-Key": self.settings.source_ba_api_key,
            "Accept": "application/json",
        }

        logger.info("ba_search query=%r location=%r radius=%s page=%s", params.get("was"), params.get("wo"), params.get("umkreis"), params.get("page"))

        try:
            http_response = self.http_transport.get_json(
                endpoint,
                params=params,
                headers=headers,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=_format_request_error(exc),
                retryable=_is_retryable_status(exc.status_code),
                status_code=exc.status_code,
                response_message=exc.message,
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"BA вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="BA search response имеет неожиданный формат верхнего уровня.",
            )

        raw_items = payload.get("ergebnisliste", payload.get("stellenangebote", []))
        if not isinstance(raw_items, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="BA search response не содержит список ergebnisliste.",
            )

        records: list[SourceRecordPreview] = []
        skipped_without_id = 0

        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                skipped_without_id += 1
                continue

            record = self._parse_record(raw_item)
            if record is None:
                skipped_without_id += 1
                continue

            records.append(record)

        warnings: tuple[str, ...] = ()
        if skipped_without_id:
            warnings = (
                f"BA вернул {skipped_without_id} запис(и) без стабильного идентификатора; они пропущены.",
            )

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            total_count=_to_int(payload.get("maxErgebnisse")),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload=dict(payload),
            warnings=warnings,
        )

    def _parse_record(self, raw_item: Mapping[str, Any]) -> SourceRecordPreview | None:
        reference = _to_text(raw_item.get("referenznummer")) or _to_text(raw_item.get("refnr"))
        external_id = reference or _to_text(raw_item.get("hashId"))
        if external_id is None:
            return None
        detail_url = _to_text(raw_item.get("externeUrl")) or _build_ba_detail_url(reference or external_id)

        return SourceRecordPreview(
            source_id=self.source_id,
            source_name=self.display_name,
            external_id=external_id,
            source_reference=reference,
            title=(
                _to_text(raw_item.get("stellenangebotsTitel"))
                or _to_text(raw_item.get("beruf"))
                or _to_text(raw_item.get("hauptberuf"))
                or "Без названия"
            ),
            company=_to_text(raw_item.get("firma")) or _to_text(raw_item.get("arbeitgeber")),
            location=_extract_location(raw_item),
            posted_at=_extract_publication_date(raw_item),
            detail_url=detail_url,
            raw_payload=dict(raw_item),
        )


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _effective_location(search_input: SourceSearchInput) -> str | None:
    """BA `wo` is a place field; `remote` should not be sent as a city."""
    if search_input.search_mode == "remote_worldwide":
        return None
    location = _to_text(search_input.location)
    if location and location.casefold() in {"deutschland", "germany"}:
        return None
    return location


def _to_int(value: Any) -> int | None:
    text = _to_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _extract_location(raw_item: Mapping[str, Any]) -> str | None:
    raw_locations = raw_item.get("stellenlokationen")
    if isinstance(raw_locations, list):
        for raw_location in raw_locations:
            if not isinstance(raw_location, Mapping):
                continue
            address = raw_location.get("adresse")
            formatted = _format_location(address)
            if formatted:
                return formatted
    return _format_location(raw_item.get("arbeitsort"))


def _extract_publication_date(raw_item: Mapping[str, Any]) -> str | None:
    direct = (
        _to_text(raw_item.get("aktuelleVeroeffentlichungsdatum"))
        or _to_text(raw_item.get("datumErsteVeroeffentlichung"))
    )
    if direct:
        return direct
    publication_range = raw_item.get("veroeffentlichungszeitraum")
    if isinstance(publication_range, Mapping):
        return _to_text(publication_range.get("von"))
    return None


def _format_location(raw_location: Any) -> str | None:
    if not isinstance(raw_location, Mapping):
        return None

    plz = _to_text(raw_location.get("plz"))
    ort = _to_text(raw_location.get("ort"))
    region = _format_enum_text(raw_location.get("bundesland") or raw_location.get("region"))
    land = _format_enum_text(raw_location.get("land"))

    parts: list[str] = []
    city_label = " ".join(part for part in (plz, ort) if part)
    if city_label:
        parts.append(city_label)

    if region and region != ort:
        parts.append(region)
    if land:
        parts.append(land)

    return ", ".join(parts) or None


def _format_enum_text(value: Any) -> str | None:
    text = _to_text(value)
    if text is None:
        return None
    if "_" in text or text.isupper():
        return text.replace("_", " ").title()
    return text


def _is_retryable_status(status_code: int | None) -> bool:
    return status_code is None or status_code == 429 or status_code >= 500


def _format_request_error(error: HttpTransportError) -> str:
    status_code = error.status_code
    if status_code == 403:
        return "BA API отклонил запрос (HTTP 403): endpoint или authentication contract недействителен."
    if status_code == 404:
        return "BA API endpoint не найден (HTTP 404)."
    if status_code == 429:
        return "BA API временно ограничил частоту запросов (HTTP 429)."
    if status_code is not None and status_code >= 500:
        return f"BA API временно недоступен (HTTP {status_code})."
    if status_code is None and "timed out" in error.message.casefold():
        return "Истекло время ожидания ответа BA API."
    status_hint = f" (HTTP {status_code})" if status_code is not None else ""
    return f"Не удалось получить ответ от BA{status_hint}: {error.message}".strip()


def _build_ba_detail_url(identifier: str) -> str:
    query = urlencode(
        {
            "angebotsart": 1,
            "id": identifier,
            "was": identifier,
        }
    )
    return f"{_BA_PUBLIC_JOBSEARCH_URL}?{query}"
