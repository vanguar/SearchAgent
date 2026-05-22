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

        endpoint = f"{self.settings.source_ba_base_url.rstrip('/')}/pc/v4/jobs"
        location = _effective_location(search_input)
        params = {
            "was": search_input.query or None,
            "wo": location or None,
            "umkreis": search_input.radius_km,
            "page": search_input.page,
            "size": search_input.page_size,
            "angebotsart": 1,
        }
        headers = {"X-API-Key": self.settings.source_ba_api_key}

        logger.info("ba_search query=%r location=%r radius=%s page=%s", params.get("was"), params.get("wo"), params.get("umkreis"), params.get("page"))

        try:
            http_response = self.http_transport.get_json(
                endpoint,
                params=params,
                headers=headers,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от BA.{status_hint} {exc.message}".strip(),
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

        raw_items = payload.get("stellenangebote", [])
        if not isinstance(raw_items, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="BA search response не содержит список stellenangebote.",
            )

        records: list[SourceRecordPreview] = []
        skipped_without_id = 0

        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise AdapterResponseError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message="BA search response содержит запись неожиданного типа.",
                )

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
        reference = _to_text(raw_item.get("refnr"))
        external_id = reference or _to_text(raw_item.get("hashId"))
        if external_id is None:
            return None
        detail_url = _to_text(raw_item.get("externeUrl")) or _build_ba_detail_url(reference or external_id)

        return SourceRecordPreview(
            source_id=self.source_id,
            source_name=self.display_name,
            external_id=external_id,
            source_reference=reference,
            title=_to_text(raw_item.get("beruf")) or "Без названия",
            company=_to_text(raw_item.get("arbeitgeber")),
            location=_format_location(raw_item.get("arbeitsort")),
            posted_at=_to_text(raw_item.get("aktuelleVeroeffentlichungsdatum")),
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
    return search_input.location


def _to_int(value: Any) -> int | None:
    text = _to_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _format_location(raw_location: Any) -> str | None:
    if not isinstance(raw_location, Mapping):
        return None

    plz = _to_text(raw_location.get("plz"))
    ort = _to_text(raw_location.get("ort"))
    region = _to_text(raw_location.get("region"))
    land = _to_text(raw_location.get("land"))

    parts: list[str] = []
    city_label = " ".join(part for part in (plz, ort) if part)
    if city_label:
        parts.append(city_label)

    if region and region != ort:
        parts.append(region)
    if land:
        parts.append(land)

    return ", ".join(parts) or None


def _build_ba_detail_url(identifier: str) -> str:
    query = urlencode(
        {
            "angebotsart": 1,
            "id": identifier,
            "was": identifier,
        }
    )
    return f"{_BA_PUBLIC_JOBSEARCH_URL}?{query}"
