"""Кадровые агентства: в конец выдачи, от агентства одна самая свежая вакансия."""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest
from app.core.relevance_config import RelevanceConfig
from app.services.employer_identity import employer_key
from app.services.search_models import FilterResult, ScoreResult, SearchResultItem, VacancySignalSnapshot
from app.services.staffing_agency import demote_staffing_agencies, staffing_agency_evidence
from tests.services.relevance_support import build_canonical, courier_run, visible_items

_DEMOTE = RelevanceConfig(staffing_agency_policy="demote")


@pytest.mark.parametrize(
    "company",
    [
        "PerZukunft Arbeitsvermittlung GmbH & Co. KG",
        "Rose Zeitarbeit Berlin/Brandenburg",
        "TimePartner Personalmanagement GmbH",
        "Time Tec Personalservice GmbH",
        "ARWA Personaldienstleistungen GmbH",
    ],
)
def test_agency_is_recognised_by_company_name(company: str) -> None:
    assert staffing_agency_evidence(build_canonical(title="Fahrer", body="Lieferungen.", company=company))


def test_agency_is_recognised_by_two_text_markers() -> None:
    canonical = build_canonical(
        title="Fahrer (m/w/d)",
        body="Für einen Kunden suchen wir Fahrer. Ein Vermittlungsgutschein kann eingelöst werden.",
        company="perZukunft",
    )

    assert staffing_agency_evidence(canonical)


@pytest.mark.parametrize(
    "body",
    [
        "Wir liefern Getränke an unsere Kunden in ganz Berlin.",
        "Wir sind keine Zeitarbeitsfirma, sondern stellen direkt ein. Unsere Kunden schätzen uns.",
    ],
)
def test_ordinary_employer_is_not_an_agency(body: str) -> None:
    assert staffing_agency_evidence(build_canonical(title="Fahrer", body=body, company="flaschenpost SE")) is None


def _item(index: int, company: str, *, posted: date, score: int, agency: bool, bucket: str = "hot") -> SearchResultItem:
    canonical = dataclasses.replace(
        build_canonical(title=f"Fahrer {index}", body="Lieferungen.", company=company, external_id=f"{company}-{index}"),
        posted_date=posted,
    )
    return SearchResultItem(
        canonical_group=canonical,
        primary_record=canonical.source_records[0],
        signals=VacancySignalSnapshot(combined_text="", staffing_agency="компания" if agency else None),
        filter_result=FilterResult(decision="allow"),
        score_result=ScoreResult(score=score),
        bucket=bucket,
        explanation_ru="",
    )


def test_agency_vacancies_go_last_and_only_the_freshest_stays() -> None:
    items = [
        _item(1, "perZukunft", posted=date(2026, 10, 1), score=95, agency=True),
        _item(2, "Deutsche Post AG", posted=date(2026, 10, 2), score=80, agency=False),
        _item(3, "PerZukunft Arbeitsvermittlung GmbH", posted=date(2026, 10, 6), score=70, agency=True, bucket="maybe"),
        _item(4, "perZukunft", posted=date(2026, 9, 1), score=99, agency=True),
        _item(5, "Picnic", posted=date(2026, 10, 3), score=60, agency=False, bucket="maybe"),
    ]

    ordered, dropped = demote_staffing_agencies(items, config=_DEMOTE)

    assert [item.primary_record.original_title for item in ordered] == ["Fahrer 2", "Fahrer 5", "Fahrer 3"]
    assert ordered[-1].bucket == "maybe"
    assert {item.primary_record.original_title for item in dropped} == {"Fahrer 1", "Fahrer 4"}


def test_keep_policy_leaves_agencies_in_place() -> None:
    items = [_item(1, "perZukunft", posted=date(2026, 10, 1), score=95, agency=True)]

    ordered, dropped = demote_staffing_agencies(items, config=RelevanceConfig(staffing_agency_policy="keep"))

    assert ordered == tuple(items) and dropped == ()


def test_real_run_shows_one_card_per_agency_at_the_end() -> None:
    result = courier_run()
    agency_cards = [item for item in visible_items(result) if item.signals.staffing_agency]
    employers = [employer_key(item.primary_record.original_company) for item in agency_cards]

    assert agency_cards
    assert len(employers) == len(set(employers))
    assert all(item.bucket == "maybe" for item in agency_cards)
    tail = result.maybe_results[-len(agency_cards):]
    assert {item.canonical_group.canonical_key for item in tail} == {
        item.canonical_group.canonical_key for item in agency_cards
    }
    assert all("кадрового агентства" in hit.label_ru for item in agency_cards for hit in item.filter_result.risk_hits
               if hit.code == "staffing_agency")
    duplicates = [item for item in result.hidden_filtered_items
                  if any(hit.code == "staffing_agency_duplicate" for hit in item.rejection_reasons)]
    assert duplicates
