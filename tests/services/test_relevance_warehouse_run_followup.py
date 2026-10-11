"""Складской прогон, второй и третий заходы: агентства по городам, признаки агентства в
тексте, устаревшие объявления, отказы модели в сводке, «Kraftfahrer», расстояние
до выбранного города и ограничения горячей категории."""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest
from app.core.relevance_config import RelevanceConfig
from app.services.employer_identity import employer_key
from app.services.filter_engine import FilterEngine
from app.services.posting_staleness import stale_posting_hit
from app.services.rule_catalog import HOT_BUCKET_MIN_SCORE, inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_models import FilterResult, ScoreResult, SearchResultItem, VacancySignalSnapshot
from app.services.source_adapters.careerjet_adapter import _effective_company
from app.services.staffing_agency import agency_brand_key, demote_staffing_agencies, staffing_agency_evidence
from app.services.translation_quality import is_valid_summary
from app.services.workplace_text import text_workplace_conflict
from tests.services.relevance_support import build_canonical
from tests.services.test_relevance_warehouse_run import _risk_codes, _warehouse_profile


def _agency_item(index: int, company: str, *, city: str, posted: date) -> SearchResultItem:
    canonical = dataclasses.replace(
        build_canonical(title=f"Lagerhelfer {index}", body="Lager.", company=company, external_id=f"a-{index}"),
        posted_date=posted,
    )
    return SearchResultItem(
        canonical_group=canonical,
        primary_record=canonical.source_records[0],
        signals=VacancySignalSnapshot(combined_text="", staffing_agency="компания", matched_search_city=city),
        filter_result=FilterResult(decision="allow"),
        score_result=ScoreResult(score=80),
        bucket="hot",
        explanation_ru="",
    )


def test_one_agency_vacancy_per_search_city() -> None:
    items = [
        _agency_item(1, "Randstad", city="berlin", posted=date(2026, 10, 1)),
        _agency_item(2, "Randstad Deutschland", city="berlin", posted=date(2026, 10, 5)),
        _agency_item(3, "Randstad", city="rostock", posted=date(2026, 10, 2)),
    ]

    ordered, dropped = demote_staffing_agencies(items, config=RelevanceConfig(staffing_agency_policy="demote"))

    assert {item.canonical_group.canonical_key for item in ordered} == {"canonical-a-2", "canonical-a-3"}
    assert [item.canonical_group.canonical_key for item in dropped] == ["canonical-a-1"]


def test_hofmann_brand_names_are_one_agency_but_not_one_employer() -> None:
    assert agency_brand_key(employer_key("I. K. Hofmann")) == agency_brand_key(employer_key("Hofmann Personal"))
    assert employer_key("I. K. Hofmann") != employer_key("Hofmann Personal")
    assert agency_brand_key(employer_key("Hofmann Bau GmbH")) != "hofmann personal"


@pytest.mark.parametrize(
    "body",
    [
        "Langfristige Anstellung, Arbeitsvertrag nach dem iGZ-Tariflohn.",
        "Bezahlung nach Tarifvertrag GVP.",
        "Wir sind Personaldienstleistung und Zeitarbeit mit Herz.",
    ],
)
def test_single_strong_text_marker_means_agency(body: str) -> None:
    assert staffing_agency_evidence(build_canonical(title="Lagerhelfer", body=body, company="Muster GmbH"))


def test_negated_zeitarbeit_is_not_an_agency() -> None:
    body = "Wir sind keine Zeitarbeitsfirma, sondern stellen direkt ein."
    assert staffing_agency_evidence(build_canonical(title="Lagerhelfer", body=body, company="Muster GmbH")) is None


@pytest.mark.parametrize(
    ("company", "description", "expected"),
    [
        ("meinestadt.de", "Arbeitgeber: Adecco Germany  Einsatzort: 10178 Berlin   Pack mit an", "Adecco Germany"),
        ("meinestadt.de", "Arbeitgeber: WrieBa GmbH Einsatzort: 10178 Berlin", "WrieBa GmbH"),
        ("meinestadt.de", "Freude am Arbeiten und Teamgeist", "meinestadt.de"),
        ("Randstad", "Arbeitgeber: Andere GmbH  Einsatzort: Berlin", "Randstad"),
    ],
)
def test_careerjet_portal_company_is_replaced_by_employer(company: str, description: str, expected: str) -> None:
    assert _effective_company(company, description) == expected


@pytest.mark.parametrize(
    ("posted", "body", "stale"),
    [
        (date(2025, 9, 1), "Lagerarbeit.", True),
        (date(2026, 9, 1), "Lagerarbeit.", False),
        (date(2026, 10, 1), "Wir suchen unbefristet ab dem 01.10.2022 oder früher 2 Mitarbeiter.", True),
        (date(2026, 10, 1), "Start ab 01.11.2026.", False),
        (date(2026, 10, 1), "Firma gegründet am 01.10.1990, Start zum 01.11.2026.", False),
        (None, "Lagerarbeit.", False),
    ],
)
def test_stale_posting(posted: date | None, body: str, stale: bool) -> None:
    hit = stale_posting_hit(posted_date=posted, raw_text=body, today=date(2026, 10, 11))
    assert (hit is not None) is stale


@pytest.mark.parametrize(
    ("summary", "valid"),
    [
        ("Извините, текст вакансии не содержит информации о конкретной позиции или требованиях.", False),
        ("К сожалению, в тексте нет информации о вакансии.", False),
        ("Работа на складе в Берлине, полный день, есть смены.", True),
    ],
)
def test_model_refusal_is_not_a_summary(summary: str, valid: bool) -> None:
    assert is_valid_summary("Lagerarbeit in Berlin, Vollzeit, Schichtarbeit.", summary) is valid


@pytest.mark.parametrize(
    ("title", "risky"),
    [
        ("Lagermitarbeiter / Kraftfahrer (m/w/d) – Veranstaltungstechnik", True),
        ("Lagermitarbeiter mit Kraftfahrertätigkeit (m/w/d)", True),
        ("Lagermitarbeiter mit Kraftfahrertätigkeit Klasse B", False),
        ("Lagermitarbeiter (m/w/d)", False),
    ],
)
def test_kraftfahrer_title_hints_truck_licence(title: str, risky: bool) -> None:
    assert ("permit_truck_license_likely" in _risk_codes(title, "Lager.", _warehouse_profile())) is risky


def _commute_hit(*, city: str | None, relocation: bool = True):
    profile = _warehouse_profile(relocation_ready=relocation, home_city="Neustrelitz")
    canonical = build_canonical(title="Lagermitarbeiter (m/w/d)", body="Lagerarbeit.")
    signals = dataclasses.replace(
        inspect_vacancy(canonical, profile),
        distance_from_home_km=95.0,
        matched_search_city=city,
        outside_requested_cities=False,
    )
    result = VacancyScorer().score(canonical, profile, signals=signals)
    return next(hit for hit in (*result.positive_hits, *result.negative_hits) if hit.code == "commute_distance")


def test_far_requested_city_is_not_penalised_when_relocation_is_ok() -> None:
    hit = _commute_hit(city="berlin")

    assert hit.weight == 0
    assert "в выбранном городе" in hit.label_ru


def test_far_distance_still_penalised_outside_requested_city_or_without_relocation() -> None:
    assert _commute_hit(city=None).weight < 0
    assert _commute_hit(city="berlin", relocation=False).weight < 0


def test_missing_truck_licence_keeps_vacancy_out_of_hot() -> None:
    canonical = build_canonical(title="Lagermitarbeiter (m/w/d) mit FS Kl. CE", body="Lagerarbeit, Vollzeit, unbefristet.")
    result = VacancyScorer().score(canonical, _warehouse_profile())

    assert result.score < HOT_BUCKET_MIN_SCORE
    assert "truck_license_review" in {hit.code for hit in result.negative_hits}


def test_feedback_penalty_keeps_vacancy_out_of_hot() -> None:
    canonical = build_canonical(title="Lagermitarbeiter (m/w/d)", body="Lagerarbeit, Vollzeit, unbefristet.")
    result = VacancyScorer().score(canonical, _warehouse_profile(), feedback_adjustment=-5)

    assert result.score < HOT_BUCKET_MIN_SCORE
    assert "feedback_penalty_review" in {hit.code for hit in result.negative_hits}


# --- Третий заход: порядок агентств по баллу и место работы из текста ---

def test_agency_tail_is_ordered_by_score_not_by_date() -> None:
    low_fresh = dataclasses.replace(
        _agency_item(1, "Randstad", city="berlin", posted=date(2026, 10, 9)),
        score_result=ScoreResult(score=70),
    )
    high_old = dataclasses.replace(
        _agency_item(2, "Tempton", city="berlin", posted=date(2026, 9, 1)),
        score_result=ScoreResult(score=90),
    )

    ordered, _ = demote_staffing_agencies([low_fresh, high_old], config=RelevanceConfig(staffing_agency_policy="demote"))

    assert [item.canonical_group.canonical_key for item in ordered] == ["canonical-a-2", "canonical-a-1"]


_GUSTROW_TEXT = (
    "Wir suchen eine/n Gabelstaplerfahrer/in (m/w/d) für einen Kunden in der Logistik- und "
    "Materialwirtschaft in Güstrow, Mecklenburg-Vorpommern."
)


@pytest.mark.parametrize(
    ("body", "location", "postal_code", "expected_place"),
    [
        (_GUSTROW_TEXT, "Kassebohm, Rostock", None, "Güstrow"),
        ("Einsatzort: Hamburg Einsatzzeit: 10:00 - 20:00 Uhr", "Wedding, Berlin", None, "Hamburg"),
        # Точный адрес источника надёжнее текста.
        (_GUSTROW_TEXT, "18055 Rostock", "18055", None),
        # Город из текста уже назван в локации («Britz, Berlin» — район Берлина).
        ("Für unseren Kunden in Berlin suchen wir Lagerhelfer.", "Britz, Berlin", None, None),
        # Рядом с указанным местом — противоречия нет.
        ("Für unseren Kunden in Rostock suchen wir Lagerhelfer.", "Kassebohm, Rostock", None, None),
        # Регион — не место работы.
        ("Für einen Kunden in Mecklenburg-Vorpommern suchen wir.", "Rostock", None, None),
    ],
)
def test_text_workplace_conflict(body: str, location: str, postal_code: str | None, expected_place: str | None) -> None:
    city = location.split(",")[0].split()[-1]
    conflict = text_workplace_conflict(body, city=city, postal_code=postal_code, location_text=location)
    assert (conflict[0] if conflict else None) == expected_place


def test_text_workplace_outside_requested_cities_is_hidden() -> None:
    profile = _warehouse_profile(search_cities=("Rostock", "Berlin"))
    canonical = build_canonical(
        title="Gabelstaplerfahrer/-in für Schubmaststapler (m/w/d)", body=_GUSTROW_TEXT,
        location="Kassebohm, Rostock", source_id="adzuna",
    )
    verdict = FilterEngine().evaluate(canonical, profile)

    assert "text_workplace_location_conflict" in {hit.code for hit in verdict.rejection_hits}


def test_text_workplace_inside_requested_cities_stays() -> None:
    profile = _warehouse_profile(search_cities=("Rostock", "Güstrow"))
    canonical = build_canonical(
        title="Gabelstaplerfahrer/-in für Schubmaststapler (m/w/d)", body=_GUSTROW_TEXT,
        location="Kassebohm, Rostock", source_id="adzuna",
    )
    verdict = FilterEngine().evaluate(canonical, profile)

    assert "text_workplace_location_conflict" not in {hit.code for hit in verdict.rejection_hits}


@pytest.mark.parametrize(
    ("body", "location", "expected_place"),
    [
        # Текст называет и место источника — мест работы несколько, а не другое место.
        ("Einsatzort: Hamburg Einsatzzeit: 10 Uhr. Medienabholung: 08.10.2026 in Berlin", "Wedding, Berlin", None),
        # Земля в локации и в тексте — не город источника.
        (_GUSTROW_TEXT, "Rostock, Mecklenburg-Vorpommern", "Güstrow"),
    ],
)
def test_text_workplace_source_place_mentions(body: str, location: str, expected_place: str | None) -> None:
    conflict = text_workplace_conflict(body, city=location.split(",")[0], postal_code=None, location_text=location)
    assert (conflict[0] if conflict else None) == expected_place
