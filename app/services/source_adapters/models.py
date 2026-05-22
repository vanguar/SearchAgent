from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from typing import Any

RawPayload = dict[str, Any] | list[Any] | str | int | float | bool | None
SearchMode = Literal["germany_local", "remote_worldwide"]


@dataclass(frozen=True, slots=True)
class SourceSearchInput:
    query: str
    location: str | None = None
    radius_km: int | None = None
    search_mode: SearchMode = "germany_local"
    page: int = 1
    page_size: int = 10
    # End-user browser IP for geo-targeting by source APIs (e.g. Careerjet user_ip).
    # None when no real client IP is available (local/server-side invocation).
    user_ip: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRecordPreview:
    source_id: str
    source_name: str
    external_id: str
    source_reference: str | None
    title: str
    company: str | None
    location: str | None
    posted_at: str | None
    detail_url: str | None
    raw_payload: RawPayload


@dataclass(frozen=True, slots=True)
class AdapterSearchResponse:
    source_id: str
    source_name: str
    records: tuple[SourceRecordPreview, ...]
    total_count: int | None
    page: int
    page_size: int
    raw_payload: RawPayload
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceAdapterDescriptor:
    source_id: str
    display_name: str
    enabled: bool
    status_label: str
    status_kind: Literal["success", "warning", "error", "disabled"] = "success"
    status_detail: str | None = None
    global_remote: bool = False
