"""Проигрывание сохранённого поискового прогона через весь конвейер, офлайн.

Фикстура прогона — это профиль, строки обратной связи и сырые записи
источников. По ней SearchService строит выдачу так же, как живой поиск, но без
сети и LLM. Используется скриптом scripts/relevance_snapshot.py (снимки «до» и
«после») и регрессионными тестами.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest import mock

from app.services.relevance_memory_service import RelevanceMemoryService
from app.services.search_models import SearchProfileContext, SearchResultItem, SearchRunResult
from app.services.search_normalizer import parse_search_cities
from app.services.search_service import SearchService
from app.services.source_adapters.models import SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.services.source_adapters.replay_adapter import build_replay_adapters

RUN_FIXTURE_VERSION = 1


def profile_from_fixture(payload: Mapping[str, Any]) -> SearchProfileContext:
    names = {field.name for field in dataclasses.fields(SearchProfileContext)}
    values: dict[str, Any] = {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in payload.items()
        if key in names
    }
    return SearchProfileContext(**values)


def replay_run_fixture(
    fixture: Mapping[str, Any],
    *,
    profile_overrides: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> SearchRunResult:
    """Выдача по фикстуре. `today` фиксирует «сегодня» для свежести объявлений."""
    adapters = build_replay_adapters(
        (record["source_name"], record["payload"]) for record in fixture["records"]
    )
    service = SearchService(registry=SourceAdapterRegistry(adapters))
    cities = fixture.get("cities") or ""
    profile = dataclasses.replace(
        profile_from_fixture(fixture["profile"]),
        search_cities=parse_search_cities(cities),
        **dict(profile_overrides or {}),
    )
    rows = [SimpleNamespace(**row) for row in fixture.get("feedback", ())]
    memory = RelevanceMemoryService().build_memory_from_rows(rows, profile_id=0)
    search_input = SourceSearchInput(query=fixture.get("query") or "", location=cities or None)

    def run() -> SearchRunResult:
        return service.search(
            search_input=search_input,
            source_ids=tuple(adapter.source_id for adapter in adapters),
            profile=profile,
            feedback_memory=memory,
            enrich_with_llm=False,
        )

    if today is None:
        return run()
    frozen = datetime(today.year, today.month, today.day, 12, tzinfo=UTC)
    with mock.patch("app.services.scorer.utc_now", return_value=frozen):
        return run()


def _item_row(item: SearchResultItem) -> dict[str, Any]:
    record = item.primary_record
    return {
        "key": item.canonical_group.canonical_key,
        "title": record.original_title,
        "company": record.original_company,
        "location": record.original_location,
        "score": item.score_result.score,
        "sources": len(item.canonical_group.source_records),
        "review": [hit.label_ru for hit in item.filter_result.review_hits],
        "risks": [hit.label_ru for hit in getattr(item.filter_result, "risk_hits", ())],
        "negative": [f"{hit.label_ru} ({hit.weight})" for hit in item.score_result.negative_hits],
        "positive": [f"{hit.label_ru} ({hit.weight})" for hit in item.score_result.positive_hits],
    }


def snapshot_of(result: SearchRunResult) -> dict[str, Any]:
    """Компактный снимок: числа по категориям и состав каждой категории."""
    hidden = [
        {
            "key": item.canonical_key,
            "title": item.title,
            "company": item.company_name,
            "reasons": [hit.label_ru for hit in item.rejection_reasons],
        }
        for item in result.hidden_filtered_items
    ]
    return {
        "counts": {
            "hot": len(result.hot_results),
            "maybe": len(result.maybe_results),
            "rejected": len(result.rejected_results),
            "hidden": len(hidden),
        },
        "hot": [_item_row(item) for item in result.hot_results],
        "maybe": [_item_row(item) for item in result.maybe_results],
        "rejected": [_item_row(item) for item in result.rejected_results],
        "hidden": hidden,
    }
