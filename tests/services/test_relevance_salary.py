"""Проблема 9: мусор в зарплатах."""
from __future__ import annotations

from dataclasses import replace

import pytest
from app.services.filter_engine import FilterEngine
from app.services.rule_catalog import inspect_vacancy
from app.services.salary_signal_extractor import extract_salary_signals
from app.services.scorer import _salary_hit
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
        ("Postbote für Briefe und Pakete in Berlin-Tempelhof", 17.92),
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


def test_tarif_stundenlohn_after_a_year_sentence_is_an_hourly_rate() -> None:
    text = (
        "more than 140 days per year.” Was wir bieten✓ 17,20 € Tarif-Stundenlohn ✓ Ein krisensicherer Arbeitsplatz"
    )
    salary = extract_salary_signals(text)

    assert salary.hourly_eur == 17.2
    assert not salary.doubtful


@pytest.mark.parametrize(
    ("body", "hourly", "weekly_hours"),
    [
        ("Ca. 1.470 € brutto im Monat bei Ø 20 Std./Woche, pünktliche Auszahlung.", 1470 / (20 * 52 / 12), 20.0),
        ("1.419 € brutto im Monat (bei 20 Stunden) plus Bonus. Mindestens 12 Std./Woche.", 1419 / (20 * 52 / 12), 20.0),
        ("Teilzeit 30h/Woche, 1.950 € brutto monatlich.", 1950 / (30 * 52 / 12), 30.0),
    ],
)
def test_monthly_amount_uses_stated_weekly_hours(body: str, hourly: float, weekly_hours: float) -> None:
    signals = inspect_vacancy(build_canonical(title="Fahrer (m/w/d)", body=body), courier_profile())

    assert signals.salary_hourly_eur == pytest.approx(hourly, rel=0.001)
    assert signals.salary_weekly_hours == weekly_hours


@pytest.mark.parametrize(
    "body",
    [
        # Суточные, а не рабочая неделя.
        "Einstiegsgehalt von 2410€ Brutto +14€ Spesen (bei 8 Stunden Abwesenheit) für eine 5 Tage Woche.",
        # Два разных числа часов — неясно, к какому относится сумма.
        "Vollzeit 40 Std./Woche oder Teilzeit 20 Std./Woche. 2.410 € brutto monatlich.",
        "Vollzeit, 40 Stunden pro Woche. 2.410 € brutto monatlich.",
        "2.410 € brutto monatlich.",
    ],
)
def test_monthly_amount_keeps_full_time_norm_without_one_clear_week(body: str) -> None:
    signals = inspect_vacancy(build_canonical(title="Fahrer (m/w/d)", body=body), courier_profile())

    assert signals.salary_hourly_eur == pytest.approx(2410 / 173.0, rel=0.001)
    assert signals.salary_weekly_hours is None


def test_metadata_amount_ignores_hours_without_explicit_link() -> None:
    # Агрегатор часто даёт годовую сумму за полную ставку: делить её на часы
    # из заголовка нельзя.
    canonical = build_canonical(
        title="Staplerfahrer im Logistikzentrum 28 Std./Woche Teilzeit (m/w/d)",
        body="Wir suchen Verstärkung im Lager.", source_id="careerjet",
    )
    record = canonical.source_records[0]
    canonical = replace(canonical, source_records=(
        replace(record, raw_payload={**record.raw_payload, "salary": "31200 EUR per year"}),
    ))

    signals = inspect_vacancy(canonical, courier_profile())

    assert signals.salary_hourly_eur == pytest.approx(31200 / 12 / 173.0, rel=0.001)
    assert signals.salary_weekly_hours is None


def test_salary_label_names_the_weekly_hours() -> None:
    canonical = build_canonical(title="Fahrer (m/w/d)", body="Ca. 1.470 € brutto im Monat bei Ø 20 Std./Woche.")
    profile = replace(courier_profile(), min_salary_eur_per_hour=16.0)
    hit = _salary_hit(inspect_vacancy(canonical, profile), profile)

    assert hit is not None and "(при 20 ч/нед)" in hit.label_ru


@pytest.mark.parametrize(
    ("text", "hourly"),
    [
        ("Arbeitsvertrag ab dem ersten Tag 16,20 € brutto pro Stunde 14 € steuerfreie Tagespauschale", 16.2),
        ("15 € pro Stunde, Tagespauschale 14 €", 15.0),
        ("17,20 € Tarif-Stundenlohn 332 € Urlaubsgeld im Juli", 17.2),
        ("Fester Stundenlohn von 13,90 € Zusätzlich Bonuszahlungen", 13.9),
    ],
)
def test_allowance_after_hourly_word_is_not_the_rate(text: str, hourly: float) -> None:
    assert extract_salary_signals(text).hourly_eur == pytest.approx(hourly)


@pytest.mark.parametrize(
    "text",
    [
        "Vollzeit 2410-3400€ zzgl. 14€ Netto/Tag Spesen",
        "Vergütung ab 17,11 € (gem. Tarifvertrag) 14 € netto Verpflegungsmehraufwand arbeitstäglich",
        "Stundenlohn 14 € brutto, 14 EUR | Tag (Spesen | netto)",
    ],
)
def test_netto_of_daily_allowance_does_not_make_the_wage_net(text: str) -> None:
    salary = extract_salary_signals(text)

    assert not salary.is_net
    assert salary.is_comparable
