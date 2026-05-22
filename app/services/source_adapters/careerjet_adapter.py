from __future__ import annotations

import base64
import hashlib
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

# User-Agent для обязательного параметра user_agent
# (Careerjet требует UA конечного пользователя; у нас — локальное приложение)
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ВАЖНО: две разных концепции IP в Careerjet Publisher API:
#
# 1. Авторизация (транспортный уровень): Careerjet сравнивает ИСХОДЯЩИЙ IP
#    HTTP-запроса сервера с IP, зарегистрированным в publisher-аккаунте.
#    Это не параметр запроса — это уровень TCP-соединения.
#
# 2. Параметр `user_ip` в запросе: IP конечного пользователя (браузера),
#    НЕ IP сервера. Используется Careerjet для geo-targeting и GDPR.
#    Careerjet требует этот параметр по документации API v4.
#
# Для локального приложения реальный IP браузера передаётся через
# SourceSearchInput.user_ip (собирается в routes из Request.client).
# Когда IP недоступен (например, при прямом server-side вызове без браузера),
# используется "127.0.0.1" как явный, задокументированный безопасный fallback.
# Это НЕ зарегистрированный outbound IP сервера — это честное обозначение
# локального источника запроса.
_LOCAL_FALLBACK_USER_IP = "127.0.0.1"


class CareerjetAdapter(BaseSourceAdapter):
    source_id = "careerjet"
    display_name = "Careerjet"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_careerjet_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        if not enabled:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=False,
                status_label="Отключен",
                status_kind="disabled",
                status_detail="SOURCE_CAREERJET_ENABLED=false.",
            )
        if not self.settings.source_careerjet_api_key:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=True,
                status_label="Не работает",
                status_kind="error",
                status_detail="Нет SOURCE_CAREERJET_API_KEY.",
            )
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=True,
            status_label="Не работает",
            status_kind="error",
            status_detail=(
                "Careerjet требует авторизованный outbound IP publisher-аккаунта; "
                "в текущих запусках источник отвечает HTTP 403 Unauthorized access from IP."
            ),
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        if not self.settings.source_careerjet_api_key:
            raise AdapterConfigurationError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=(
                    "Careerjet требует API-ключ. "
                    "Зарегистрируйтесь на https://www.careerjet.de/publisher/ "
                    "и задайте SOURCE_CAREERJET_API_KEY в .env."
                ),
            )

        params: dict[str, Any] = {
            "keywords": search_input.query or None,
            "location": "Remote" if search_input.search_mode == "remote_worldwide" else "Deutschland",
            "locale_code": "de_DE",
            "page": search_input.page,
            "page_size": min(search_input.page_size, 100),
            "sort": "date",
            "fragment_size": self.settings.source_careerjet_fragment_size,
            "user_agent": _DEFAULT_USER_AGENT,
            # Required by Careerjet API v4 for geo-targeting (not for auth).
            # Use the real end-user IP when available; fall back to local sentinel.
            "user_ip": search_input.user_ip or _LOCAL_FALLBACK_USER_IP,
        }
        if search_input.radius_km is not None:
            params["radius"] = search_input.radius_km

        # Basic Auth: base64(api_key + ":")
        credentials = base64.b64encode(
            f"{self.settings.source_careerjet_api_key}:".encode()
        ).decode()
        headers = {"Authorization": f"Basic {credentials}"}

        try:
            http_response = self.http_transport.get_json(
                self.settings.source_careerjet_base_url,
                params=params,
                headers=headers,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Careerjet.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Careerjet вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Careerjet ответ имеет неожиданный формат верхнего уровня.",
            )

        response_type = _to_text(payload.get("type"))
        if response_type == "LOCATIONS":
            # API не нашёл локацию или нашёл несколько — возвращаем пустой ответ
            message = _to_text(payload.get("message")) or "location mode"
            return AdapterSearchResponse(
                source_id=self.source_id,
                source_name=self.display_name,
                records=(),
                total_count=0,
                page=search_input.page,
                page_size=search_input.page_size,
                raw_payload=dict(payload),
                warnings=(f"Careerjet: режим локации — {message}.",),
            )

        raw_jobs = payload.get("jobs", [])
        if not isinstance(raw_jobs, list):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Careerjet ответ не содержит список jobs.",
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

        warnings: tuple[str, ...] = ()
        if skipped:
            warnings = (f"Careerjet: пропущено {skipped} записей без стабильного ID.",)

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            total_count=_to_int(payload.get("hits")),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload=dict(payload),
            warnings=warnings,
        )


def _parse_record(
    source_id: str,
    source_name: str,
    raw_job: Mapping[str, Any],
) -> SourceRecordPreview | None:
    # API v4 не возвращает поле id — используем хэш URL как стабильный внешний ID
    url = _to_text(raw_job.get("url"))
    if not url:
        return None

    external_id = "cj-" + hashlib.md5(url.encode()).hexdigest()[:16]

    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=url,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=_to_text(raw_job.get("company")),
        location=_to_text(raw_job.get("locations")),
        posted_at=_to_text(raw_job.get("date")),
        detail_url=url,
        raw_payload=dict(raw_job),
    )


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
