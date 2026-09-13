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


class JoobleAdapter(BaseSourceAdapter):
    source_id = "jooble"
    display_name = "Jooble"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_jooble_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        if not enabled:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=False,
                status_label="Отключен",
                status_kind="disabled",
                status_detail="SOURCE_JOOBLE_ENABLED=false.",
            )
        if not self.settings.jooble_api_key:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=True,
                status_label="Не работает",
                status_kind="error",
                status_detail="Нет JOOBLE_API_KEY.",
            )
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=True,
            status_label="Готов",
            status_kind="success",
            status_detail=(
                "REST API Jooble; нужен API key. Ключ страновой: действует для одного "
                "домена (de.jooble.org, ua.jooble.org), общий хост отдаёт ноль на любой запрос."
            ),
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        if not self.settings.jooble_api_key:
            raise AdapterConfigurationError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Jooble требует JOOBLE_API_KEY в .env.",
            )

        endpoint = f"{self.settings.source_jooble_base_url.rstrip('/')}/{self.settings.jooble_api_key}"
        body: dict[str, Any] = {
            "keywords": search_input.query,
            "page": str(max(1, search_input.page)),
            "ResultOnPage": search_input.page_size,
            "companysearch": "false",
        }
        if search_input.search_mode != "remote_worldwide":
            body["location"] = search_input.location or "Germany"
        elif search_input.location:
            body["location"] = search_input.location
        radius = _jooble_radius(search_input.radius_km)
        if radius is not None:
            body["radius"] = radius

        try:
            http_response = self.http_transport.post_json(
                endpoint,
                body=body,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Jooble.{status_hint} {exc.message}".strip(),
                status_code=exc.status_code,
                response_message=exc.message,
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Jooble вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Jooble ответ имеет неожиданный формат верхнего уровня.",
            )
        raw_jobs = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Jooble ответ не содержит список jobs.",
            )

        records: list[SourceRecordPreview] = []
        skipped = 0
        for raw_job in raw_jobs:
            if not isinstance(raw_job, Mapping):
                skipped += 1
                continue
            record = _parse_record(self.source_id, self.display_name, raw_job)
            if record is None:
                skipped += 1
                continue
            records.append(record)

        warnings_out: list[str] = []
        if skipped:
            warnings_out.append(f"Jooble: пропущено {skipped} записей без стабильного ID.")
        if not records and _to_int(payload.get("totalCount")) == 0:
            # Jooble отвечает 200 и пустым телом и на «ничего не нашлось», и на ключ,
            # выданный под другую страну: его API страновой (de.jooble.org, ua.jooble.org),
            # а общий хост отдаёт ноль на любой запрос. Молчаливый ноль неотличим от
            # честного пустого поиска, поэтому говорим об этом прямо.
            warnings_out.append(
                "Jooble ответил без ошибки, но не вернул ни одной вакансии. "
                f"API Jooble страновой, сейчас используется {self.settings.source_jooble_base_url}. "
                "Если ноль приходит на любой запрос — ключ выдан под другой домен: "
                "проверьте SOURCE_JOOBLE_BASE_URL (например https://de.jooble.org/api) и JOOBLE_API_KEY."
            )
        warnings = tuple(warnings_out)
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            total_count=_to_int(payload.get("totalCount")),
            page=max(1, search_input.page),
            page_size=search_input.page_size,
            raw_payload=dict(payload),
            warnings=warnings,
        )


def _parse_record(source_id: str, source_name: str, raw_job: Mapping[str, Any]) -> SourceRecordPreview | None:
    external_id = _to_text(raw_job.get("id")) or _to_text(raw_job.get("link"))
    if external_id is None:
        return None
    raw_payload = dict(raw_job)
    salary = _to_text(raw_job.get("salary"))
    if salary:
        raw_payload["salary"] = salary
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=_to_text(raw_job.get("company")),
        location=_to_text(raw_job.get("location")),
        posted_at=_to_text(raw_job.get("updated")),
        detail_url=_to_text(raw_job.get("link")),
        raw_payload=raw_payload,
    )


def _jooble_radius(radius_km: int | None) -> str | None:
    if radius_km is None:
        return None
    allowed = (0, 4, 8, 16, 26, 40, 80)
    return str(min(allowed, key=lambda value: abs(value - radius_km)))


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
