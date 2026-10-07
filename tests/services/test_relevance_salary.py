"""Проблема 9: мусор в зарплатах."""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine
from app.services.rule_catalog import inspect_vacancy
from app.services.salary_signal_extractor import extract_salary_signals
from tests.services.relevance_support import (
    build_canonical,
    courier_profile,
    courier_run_with_agencies,
    flatten_cards,
    items_titled,
)


def _all_cards():
    result = courier_run_with_agencies()
    return flatten_cards((*result.hot_results, *result.maybe_results, *result.rejected_results))


@pytest.mark.parametrize(
    ("text", "hourly"),
    [
        ("18,50 € Tarif-Stundenlohn inkl. 50% Weihnachtsgeld", 18.5),
        ("Fahrer - Arztpraxen - 14,25 €/h (m/w/d)", 14.25),
        ("15,00 € bis 18,00 € pro Stunde", 15.0),
        ("Vollzeit 2410-3400€ zzgl. 14€ Netto/Tag Spesen", 2410 / 173.0),
    ],
)
def test_rate_is_read_without_percent_or_allowance_noise(text: str, hourly: float) -> None:
    assert extract_salary_signals(text).hourly_eur == pytest.approx(hourly, rel=0.001)


@pytest.mark.parametrize("text", ["Vergütung von 2900,00 Euro die Stunde", "8.400 € pro Monat", "9 € pro Stunde"])
def test_amounts_outside_configured_bounds_are_doubtful_and_unused(text: str) -> None:
    salary = extract_salary_signals(text)

    assert salary.hourly_eur is None
    assert salary.doubtful


@pytest.mark.parametrize("text", ["Paketzustellung bis zu 4612,22 Euro netto", "up to 18 € per hour", "max. 3.000 € monatlich"])
def test_upper_bound_is_not_a_rate(text: str) -> None:
    salary = extract_salary_signals(text)

    assert salary.hourly_eur is None
    assert salary.upper_bound_only


def test_body_has_priority_over_title_and_metadata_is_last() -> None:
    canonical = build_canonical(
        title="Kurierfahrer 14,00 €/h (m/w/d)", body="Wir zahlen 16,00 € pro Stunde.", source_id="careerjet",
    )

    assert inspect_vacancy(canonical, courier_profile()).salary_hourly_eur == 16.0


def test_net_salary_is_flagged_and_not_comparable() -> None:
    canonical = build_canonical(title="Kurier (m/w/d)", body="Nettogehalt von 2200,00 Euro im Monat.")
    signals = inspect_vacancy(canonical, courier_profile())
    verdict = FilterEngine().evaluate(canonical, courier_profile(), signals=signals)

    assert signals.salary_is_net
    assert not signals.salary_is_comparable
    assert "salary_net" in {hit.code for hit in verdict.risk_hits}


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("Postbote für Pakete und Briefe (m/w/d)", 18.5),
        ("Fahrer - Arztpraxen", 13.9),
    ],
)
def test_real_run_rates_are_plausible(fragment: str, expected: float) -> None:
    cards = items_titled(_all_cards(), fragment)

    assert cards
    rates = {card.signals.salary_hourly_eur for card in cards}
    assert expected in rates
    assert all(rate is None or 12 <= rate <= 30 for rate in rates)


def test_real_run_upper_bound_title_gives_no_rate_from_title() -> None:
    card = items_titled(_all_cards(), "bis zu 4612,22")[0]

    assert card.signals.salary_hourly_eur != pytest.approx(22.0)
    assert card.signals.salary_is_net
    assert not card.signals.salary_is_comparable


def test_real_run_contradicting_rates_are_doubtful() -> None:
    card = items_titled(_all_cards(), "Hilfskraft / Logistik")[0]

    assert card.signals.salary_hourly_eur is None
    assert "salary_doubtful" in {hit.code for hit in card.filter_result.risk_hits}


def test_real_run_time_tec_metadata_month_is_not_shown_as_rate() -> None:
    for card in items_titled(_all_cards(), "Fahrer (m/w/d)", company="Time Tec"):
        assert card.signals.salary_hourly_eur is None or card.signals.salary_hourly_eur <= 30
