"""Проблема 11: дом по расписанию и риск «не успеть к началу смены»."""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest
from app.core.relevance_config import RelevanceConfig
from app.services.commute_signals import effective_home_city, has_early_shift, parse_home_schedule
from app.services.filter_engine import FilterEngine
from app.services.rule_catalog import inspect_vacancy
from tests.services.relevance_support import build_canonical, courier_profile

_SCHEDULE = RelevanceConfig(home_city_schedule="2026-11-01=Neustrelitz; 2027-05-01=Rostock")


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 10, 7), "Berlin"), (date(2026, 11, 1), "Neustrelitz"), (date(2027, 6, 1), "Rostock")],
)
def test_home_city_follows_the_schedule(today: date, expected: str) -> None:
    assert effective_home_city("Berlin", today=today, config=_SCHEDULE) == expected


def test_malformed_schedule_entries_are_ignored() -> None:
    assert parse_home_schedule("garbage; 2026-13-40=X; 2026-11-01 = Neustrelitz") == ((date(2026, 11, 1), "Neustrelitz"),)


@pytest.mark.parametrize(
    "text",
    ["Nachtschicht", "Zustellung zwischen 02:00 und 06:00 Uhr", "Arbeitsbeginn 4:30 Uhr", "Frühaufsteher gesucht", "ab 2 Uhr"],
)
def test_early_shift_is_recognised(text: str) -> None:
    assert has_early_shift(text)


@pytest.mark.parametrize("text", ["Frühschicht ab 6 Uhr", "Arbeitszeit 8:00 bis 16:00", "Start am 01.11.2026", "keine Nachtschicht"])
def test_normal_start_is_not_an_early_shift(text: str) -> None:
    assert not has_early_shift(text)


def _risk_codes(location: str, body: str) -> set[str]:
    profile = dataclasses.replace(courier_profile(), home_city="Neustrelitz")
    canonical = build_canonical(title="Zeitungszusteller (m/w/d)", body=body, location=location)
    signals = inspect_vacancy(canonical, profile)
    return {hit.code for hit in FilterEngine().evaluate(canonical, profile, signals=signals).risk_hits}


def test_far_workplace_with_night_start_is_a_risk() -> None:
    assert "early_shift_long_commute" in _risk_codes("Berlin", "Zustellung zwischen 02:00 und 06:00 Uhr.")


def test_near_workplace_with_night_start_is_not_a_risk() -> None:
    assert "early_shift_long_commute" not in _risk_codes("Neustrelitz", "Zustellung zwischen 02:00 und 06:00 Uhr.")


def test_far_workplace_with_day_start_is_not_a_risk() -> None:
    assert "early_shift_long_commute" not in _risk_codes("Berlin", "Arbeitszeit 8:00 bis 16:00 Uhr.")
