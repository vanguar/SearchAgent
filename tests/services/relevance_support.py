"""Общие помощники для тестов релевантности на реальном прогоне курьера."""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.relevance_replay import profile_from_fixture, replay_run_fixture
from app.services.search_models import SearchProfileContext, SearchResultItem, SearchRunResult
from app.services.source_adapters.models import SourceRecordPreview

COURIER_RUN_FIXTURE = Path(__file__).parents[1] / "fixtures" / "relevance_run_courier_2026-10-07.json"
RUN_DAY = date(2026, 10, 7)


@lru_cache(maxsize=1)
def courier_fixture() -> dict[str, Any]:
    return json.loads(COURIER_RUN_FIXTURE.read_text(encoding="utf-8"))


@lru_cache(maxsize=2)
def courier_run() -> SearchRunResult:
    """Реальный прогон «Доставка / Курьер» 07.10.2026 через текущий конвейер."""
    return replay_run_fixture(courier_fixture(), today=RUN_DAY)


def courier_profile(**overrides: Any) -> SearchProfileContext:
    profile = profile_from_fixture(courier_fixture()["profile"])
    return dataclasses.replace(profile, **overrides) if overrides else profile


def build_canonical(
    *,
    title: str,
    body: str,
    company: str = "Muster Logistik GmbH",
    location: str = "Berlin",
    source_id: str = "ba",
    external_id: str = "fixture-1",
) -> CanonicalVacancyGroup:
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id=source_id,
            source_name=source_id.upper(),
            external_id=external_id,
            source_reference=external_id,
            title=title,
            company=company,
            location=location,
            posted_at="2026-10-05",
            detail_url=f"https://example.org/jobs/{external_id}",
            raw_payload={"description": body},
        )
    )
    return CanonicalVacancyGroup(
        canonical_key=f"canonical-{external_id}",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=(record.source_record_key,),
    )


def flatten_cards(items: Any) -> tuple[SearchResultItem, ...]:
    """Карточки вместе со свёрнутыми в них вакансиями того же работодателя."""
    return tuple(card for item in items for card in (item, *item.cluster_members))


def visible_items(result: SearchRunResult) -> tuple[SearchResultItem, ...]:
    """Всё, что человек видит: карточки «горячих» и «на проверку» и свёрнутое в них."""
    return flatten_cards((*result.hot_results, *result.maybe_results))


def items_titled(items: Any, fragment: str, *, company: str | None = None) -> list[Any]:
    """Карточки (или скрытые записи), чей заголовок содержит фрагмент."""
    found = []
    for item in items:
        title = getattr(item, "title", None) or item.primary_record.original_title
        employer = getattr(item, "company_name", None)
        if employer is None and hasattr(item, "primary_record"):
            employer = item.primary_record.original_company or item.canonical_group.company_name
        if fragment.casefold() not in title.casefold():
            continue
        if company is not None and company.casefold() not in (employer or "").casefold():
            continue
        found.append(item)
    return found


def hidden_reasons(result: SearchRunResult, fragment: str) -> list[str]:
    return [
        hit.code
        for item in items_titled(result.hidden_filtered_items, fragment)
        for hit in item.rejection_reasons
    ]
