"""Проблема 2: вакансии не на машине."""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine
from app.services.profile_form_spec import parse_profile_form
from app.services.scorer import VacancyScorer
from tests.services.relevance_support import (
    build_canonical,
    courier_profile,
    courier_run,
    hidden_reasons,
    items_titled,
    visible_items,
)

CAR_PROFILE = {"transport_modes": ("car", "van")}


def _verdict(title: str, body: str, **overrides: object):
    profile = courier_profile(**{**CAR_PROFILE, **overrides})
    return FilterEngine().evaluate(build_canonical(title=title, body=body), profile)


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Kurierfahrer (m/w/d) mit e-Bike/Fahrrad - Berlin", "Lieferungen im Stadtgebiet."),
        ("Pizzafahrer:in - Rad - Domino's Berlin Weißensee", "Lieferung von Pizzen."),
        ("(MINIJOB) RIDER / Cargo Bike Kurier / Paketzusteller", "Pakete im Kiez ausliefern."),
        ("Lastenrad-Kurier (m/w/d)", "Auslieferung mit dem Lastenrad."),
        ("Kurier (m/w/d)", "Die Auslieferung erfolgt per Fahrrad im Innenstadtbereich."),
        ("Zusteller (m/w/d)", "Die Zustellung erfolgt zu Fuß in deinem Wohngebiet."),
    ],
)
def test_bike_or_foot_jobs_are_hidden_for_car_profile(title: str, body: str) -> None:
    verdict = _verdict(title, body)

    hits = [hit for hit in verdict.rejection_hits if hit.code == "transport_mode_mismatch"]
    assert hits, title
    assert "машин" in hits[0].label_ru


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Kurierfahrer PKW oder Fahrrad Minijob", "Du fährst mit dem Auto."),
        ("Kurier (m/w/d)", "Auslieferung per Fahrrad oder mit dem Firmenwagen."),
        ("Kurier Last Mile Logistics mit Auto", "Pakete im Stadtgebiet."),
        ("Fahrer (m/w/d) für Radlader-Transporte", "Transporter Klasse B."),
    ],
)
def test_car_alternative_keeps_vacancy(title: str, body: str) -> None:
    verdict = _verdict(title, body)

    assert "transport_mode_mismatch" not in {hit.code for hit in verdict.rejection_hits}


def test_bike_job_stays_when_profile_allows_bike() -> None:
    verdict = _verdict("Kurierfahrer mit e-Bike", "Lieferungen.", transport_modes=("car", "bike"))

    assert "transport_mode_mismatch" not in {hit.code for hit in verdict.rejection_hits}


def test_bike_job_stays_when_profile_has_no_transport_modes() -> None:
    verdict = _verdict("Kurierfahrer mit e-Bike", "Lieferungen.", transport_modes=())

    assert "transport_mode_mismatch" not in {hit.code for hit in verdict.rejection_hits}


def test_newspaper_delivery_is_demoted_for_car_profile() -> None:
    profile = courier_profile(**CAR_PROFILE)
    canonical = build_canonical(
        title="Zeitungszusteller (m/w/d)",
        body="Frühaufsteher gesucht! Zustellung der Tageszeitung zwischen 02:00 und 06:00 Uhr.",
    )
    baseline_profile = courier_profile(transport_modes=())

    score = VacancyScorer().score(canonical, profile)
    baseline = VacancyScorer().score(canonical, baseline_profile)

    assert any(hit.code == "press_distribution_non_target" for hit in score.negative_hits)
    assert score.score < baseline.score


def test_profile_form_parses_transport_modes_in_fixed_order() -> None:
    fields = parse_profile_form({"transport_modes": ["van", "car", "plane"], "transport_modes_present": "1"})

    assert fields.transport_modes == ("car", "van")
    assert fields.was_submitted("transport_modes")


@pytest.mark.parametrize(
    "fragment",
    ["Kurierfahrer (m/w/d) mit e-Bike/Fahrrad", "Pizzafahrer:in - Rad", "RIDER / Cargo Bike"],
)
def test_real_run_hides_bike_jobs(fragment: str) -> None:
    result = courier_run()

    assert not items_titled(visible_items(result), fragment), fragment
    assert "transport_mode_mismatch" in hidden_reasons(result, fragment)


def test_real_run_keeps_car_courier_jobs() -> None:
    result = courier_run()

    assert items_titled(visible_items(result), "Last-Mile Logistics mit Auto")
