from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError, HttpTransportError
from app.services.source_adapters.http import HttpTextTransport, UrllibHttpTextTransport
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.rss import parse_rss_items


class DouRssAdapter(BaseSourceAdapter):
    source_id = "dou_rss"
    display_name = "DOU Jobs RSS"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        text_transport: HttpTextTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.text_transport = text_transport or UrllibHttpTextTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_dou_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail=(
                "Украинский IT-источник через RSS DOU; без HTML detail fetch."
                if enabled
                else "SOURCE_DOU_ENABLED=false."
            ),
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        query = search_input.query.strip()
        params: dict[str, Any] = {}
        category = _category_for_query(query)
        if category:
            params["category"] = category
        elif query:
            params["search"] = query

        try:
            response = self.text_transport.get_text(
                self.settings.source_dou_feed_url,
                params=params,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0 Safari/537.36 SmartJobSearchAgent/0.1"
                    )
                },
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            if _is_cloudflare_access_denied(exc):
                raise AdapterRequestError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message=(
                        "DOU заблокировал RSS-запрос через Cloudflare "
                        "(HTTP 403 / Error 1010: access denied by browser signature). "
                        "Это ограничение jobs.dou.ua; временно отключите источник через "
                        "SOURCE_DOU_ENABLED=false или повторите позже."
                    ),
                    retryable=False,
                ) from exc
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить RSS DOU.{status_hint} {exc.message}".strip(),
            ) from exc

        items = parse_rss_items(response.text, source_id=self.source_id, source_name=self.display_name)
        records = tuple(
            record
            for item in items
            if (record := _item_to_record(self.source_id, self.display_name, item.raw_payload)) is not None
        )

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=records,
            total_count=len(records),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"feed_url": response.url, "item_count": len(items)},
        )


def _category_for_query(query: str) -> str | None:
    normalized = query.lower()
    if "python" in normalized:
        return "Python"
    if "devops" in normalized:
        return "DevOps"
    if any(token in normalized for token in ("ai", "ml", "machine learning")):
        return "AI/ML"
    return None


def _is_cloudflare_access_denied(exc: HttpTransportError) -> bool:
    message = exc.message.casefold()
    return exc.status_code == 403 and (
        "cloudflare" in message
        or "error 1010" in message
        or "browser's signature" in message
        or "access denied" in message
    )


def _item_to_record(source_id: str, source_name: str, raw_item: dict[str, Any]) -> SourceRecordPreview | None:
    external_id = _to_text(raw_item.get("guid")) or _to_text(raw_item.get("link"))
    if external_id is None:
        return None
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_item.get("title")) or "Без названия",
        company=None,
        location=None,
        posted_at=_to_text(raw_item.get("pub_date")),
        detail_url=_to_text(raw_item.get("link")),
        raw_payload=dict(raw_item),
    )


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
