from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import Settings
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

# EURES EAPI v2 — поиск вакансий по стране DE
# Регистрация API-ключа (бесплатно): https://eures.europa.eu/en/find-a-job/eures-job-search-api
_EURES_DETAIL_BASE = "https://eures.europa.eu/jobs/"


class EURESAdapter(BaseSourceAdapter):
    source_id = "eures"
    display_name = "EURES"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_eures_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        if not enabled:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=False,
                status_label="Отключен",
                status_kind="disabled",
                status_detail="SOURCE_EURES_ENABLED=false.",
                global_remote=True,
            )
        if not self.settings.source_eures_api_key:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=True,
                status_label="Не работает",
                status_kind="error",
                status_detail=(
                    "Нет SOURCE_EURES_API_KEY. Ключ бесплатный: "
                    "https://eures.europa.eu/eures-and-you/employers/eures-job-search-api_en — "
                    "официальный портал ЕС, все страны Союза, без России и Беларуси."
                ),
                global_remote=True,
            )
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=True,
            status_label="Готов",
            status_kind="success",
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        if not self.settings.source_eures_api_key:
            raise AdapterConfigurationError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=(
                    "EURES требует API-ключ. "
                    "Зарегистрируйтесь бесплатно на "
                    "https://eures.europa.eu/en/find-a-job/eures-job-search-api "
                    "и задайте SOURCE_EURES_API_KEY в .env."
                ),
            )

        # EURES EAPI v2 использует POST с JSON-телом; страницы с 0
        page_zero_based = max(0, search_input.page - 1)
        body: dict[str, Any] = {
            "keywords": search_input.query or "",
            "sortBy": "BEST_MATCH",
            "resultsPerPage": search_input.page_size,
            "page": page_zero_based,
        }
        if search_input.search_mode != "remote_worldwide":
            body["countries"] = ["DE"]
        headers = {
            "User-Api-Key": self.settings.source_eures_api_key,
        }

        try:
            http_response = self.http_transport.post_json(
                self.settings.source_eures_base_url,
                body=body,
                headers=headers,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от EURES.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"EURES вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="EURES ответ имеет неожиданный формат верхнего уровня.",
            )

        data = payload.get("data")
        if data is None or not isinstance(data, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="EURES ответ не содержит поле 'data'.",
            )

        raw_items = data.get("payload", [])
        if not isinstance(raw_items, (list, tuple)):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="EURES data.payload не является списком.",
            )

        records: list[SourceRecordPreview] = []
        skipped = 0

        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                skipped += 1
                continue
            record = _parse_record(self.source_id, self.display_name, raw_item)
            if record is None:
                skipped += 1
                continue
            records.append(record)

        warnings: tuple[str, ...] = ()
        if skipped:
            warnings = (f"EURES: пропущено {skipped} записей без стабильного ID.",)

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            total_count=_to_int(data.get("totalMatchingCount")),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload=dict(payload),
            warnings=warnings,
        )


def _parse_record(
    source_id: str,
    source_name: str,
    raw_item: Mapping[str, Any],
) -> SourceRecordPreview | None:
    header = raw_item.get("header") or {}
    if not isinstance(header, Mapping):
        header = {}

    external_id = _to_text(header.get("handle")) or _to_text(raw_item.get("jvId"))
    if not external_id:
        return None

    jv = raw_item.get("jobVacancy") or {}
    if not isinstance(jv, Mapping):
        jv = {}

    title = _to_text(jv.get("jobTitle")) or "Без названия"
    company = _extract_employer(jv)
    location = _extract_location(jv)
    posted_at = _to_text(header.get("dateCreated"))
    detail_url = _extract_url(raw_item)

    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=title,
        company=company,
        location=location,
        posted_at=posted_at,
        detail_url=detail_url or f"{_EURES_DETAIL_BASE}{external_id}",
        raw_payload=dict(raw_item),
    )


def _extract_employer(jv: Mapping[str, Any]) -> str | None:
    employer = jv.get("employer")
    if isinstance(employer, Mapping):
        return _to_text(employer.get("name"))
    return None


def _extract_location(jv: Mapping[str, Any]) -> str | None:
    pos = jv.get("positionLocation")
    if isinstance(pos, Mapping):
        city = _to_text(pos.get("cityName"))
        country = _to_text(pos.get("countryCode"))
        parts = [p for p in (city, country) if p]
        return ", ".join(parts) or None
    if isinstance(pos, (list, tuple)) and pos:
        first = pos[0]
        if isinstance(first, Mapping):
            city = _to_text(first.get("cityName"))
            country = _to_text(first.get("countryCode"))
            parts = [p for p in (city, country) if p]
            return ", ".join(parts) or None
    return None


def _extract_url(raw_item: Mapping[str, Any]) -> str | None:
    urls = raw_item.get("urls")
    if isinstance(urls, (list, tuple)) and urls:
        first = urls[0]
        if isinstance(first, Mapping):
            return _to_text(first.get("url"))
        if isinstance(first, str):
            return first or None
    return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int | None:
    text = _to_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None
