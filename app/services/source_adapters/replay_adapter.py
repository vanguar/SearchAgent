"""Офлайн-источник: отдаёт заранее сохранённые записи.

Нужен, чтобы прогнать весь конвейер (нормализация, дедупликация, фильтры,
скоринг) на реальных объявлениях из прошлого прогона — без сети. Сырой payload
разбирается теми же функциями, что и в боевых адаптерах, поэтому запись
получается ровно такой, какой её видел живой поиск.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.source_adapters import adzuna_adapter, careerjet_adapter
from app.services.source_adapters.ba_adapter import BAAdapter
from app.services.source_adapters.base import SourceAdapter
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceRecordPreview,
    SourceSearchInput,
)

# Имя источника в базе → идентификатор адаптера.
STORED_SOURCE_IDS: dict[str, str] = {
    "Careerjet": "careerjet",
    "Adzuna": "adzuna",
    "BA (Bundesagentur fur Arbeit)": "ba",
}


class _BAParserContext:
    source_id = "ba"
    display_name = "BA (Bundesagentur fur Arbeit)"


_BA_CONTEXT = _BAParserContext()


def parse_stored_payload(source_name: str, payload: Mapping[str, Any]) -> SourceRecordPreview | None:
    """Сырой payload источника → запись адаптера. None — источник не поддерживается."""
    source_id = STORED_SOURCE_IDS.get(source_name)
    if source_id == "careerjet":
        return careerjet_adapter._parse_record(source_id, source_name, payload)
    if source_id == "adzuna":
        return adzuna_adapter._parse_record(source_id, source_name, payload)
    if source_id == "ba":
        return BAAdapter._parse_record(_BA_CONTEXT, payload)  # type: ignore[arg-type]
    return None


class ReplayAdapter(SourceAdapter):
    """Источник, который на любой запрос отдаёт один и тот же набор записей."""

    def __init__(self, source_id: str, display_name: str, records: Iterable[SourceRecordPreview]) -> None:
        self.source_id = source_id
        self.display_name = display_name
        self._records = tuple(records)

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=self._records,
            total_count=len(self._records),
            page=1,
            page_size=len(self._records),
            raw_payload=None,
        )


def build_replay_adapters(stored: Iterable[tuple[str, Mapping[str, Any]]]) -> tuple[ReplayAdapter, ...]:
    """(имя источника, payload) → по одному офлайн-адаптеру на источник."""
    by_source: dict[str, list[SourceRecordPreview]] = {}
    names: dict[str, str] = {}
    for source_name, payload in stored:
        record = parse_stored_payload(source_name, payload)
        if record is None:
            continue
        by_source.setdefault(record.source_id, []).append(record)
        names[record.source_id] = source_name
    return tuple(
        ReplayAdapter(source_id, names[source_id], records)
        for source_id, records in sorted(by_source.items())
    )
