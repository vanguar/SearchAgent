"""«Не должно сломаться»: лучшие вакансии остаются видимыми не ниже «на проверку»."""
from __future__ import annotations

import pytest
from app.services.search_service import SearchService
from app.services.source_merge import SourceMergeService
from tests.services.relevance_support import courier_run, items_titled, visible_items
from tests.services.test_live_vehicle_logistics_regressions import PROFILE as TRANSFER_PROFILE
from tests.services.test_live_vehicle_logistics_regressions import live_record


@pytest.mark.parametrize(
    ("fragment", "company"),
    [
        ("Paketzusteller in Vollzeit", "Deutsche Post"),
        ("Postbote für Briefe und Pakete in Berlin-Tempelhof", "Deutsche Post"),
        ("Fahrer / Kurier (m/w/d) - gerne Quereinsteiger", "flaschenpost"),
        ("Fahrer:in / Reiniger:in Carsharing", "MILES"),
        ("Fahrer (m/w/d)", "IRS"),
        ("Auslieferungsfahrer", "Coolblue"),
    ],
)
def test_best_courier_vacancies_stay_visible(fragment: str, company: str) -> None:
    cards = items_titled(visible_items(courier_run()), fragment, company=company)

    assert cards, (fragment, company)
    assert all(card.bucket in {"hot", "maybe"} for card in cards)


@pytest.mark.parametrize("company", ["Lalamove", "Otoqi", "Good Bank"])
def test_freelance_vacancies_keep_the_not_employment_risk(company: str) -> None:
    cards = items_titled(visible_items(courier_run()), "", company=company)

    assert cards
    for card in cards:
        assert "self_employment_review" in {hit.code for hit in card.filter_result.review_hits}


def test_agency_vehicle_transfer_with_class_b_stays_visible() -> None:
    record = live_record("cj-f0c8caf613a29c78")
    assert "berfuhrer" in record.normalized_title and "perzukunft" in (record.normalized_company or "")
    canonical = SourceMergeService().merge_records((record,)).canonical_groups[0]
    service = SearchService()

    item = service._build_result_item(canonical=canonical, profile=TRANSFER_PROFILE, enrich_with_llm=False)

    assert item is not None
    assert item.bucket in {"hot", "maybe"}
