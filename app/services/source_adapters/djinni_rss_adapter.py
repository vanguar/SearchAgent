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


class DjinniRssAdapter(BaseSourceAdapter):
    source_id = "djinni_rss"
    display_name = "Djinni Jobs RSS"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        text_transport: HttpTextTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.text_transport = text_transport or UrllibHttpTextTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_djinni_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail=(
                "Украинский/remote IT-источник через RSS Djinni. В фиде есть только заголовок, "
                "ссылка и описание: компанию и город Djinni не отдаёт, поэтому на карточках "
                "этих вакансий они остаются пустыми."
                if enabled
                else "SOURCE_DJINNI_ENABLED=false."
            ),
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        params: dict[str, Any] = {}
        if search_input.query:
            params["primary_keyword"] = search_input.query

        try:
            response = self.text_transport.get_text(
                self.settings.source_djinni_feed_url,
                params=params,
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить RSS Djinni.{status_hint} {exc.message}".strip(),
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
