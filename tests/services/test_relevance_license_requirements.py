"""Проблема 1: категории прав, P-Schein, Krankentransport, риски по допускам."""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine
from app.services.license_requirement_signals import extract_license_requirement_signals
from app.services.search_models import parse_license_classes
from tests.services.relevance_support import (
    build_canonical,
    courier_profile,
    courier_run,
    hidden_reasons,
    items_titled,
    visible_items,
)


def _verdict(title: str, body: str, **profile_overrides: object):
    return FilterEngine().evaluate(build_canonical(title=title, body=body), courier_profile(**profile_overrides))


def _codes(hits) -> set[str]:
    return {hit.code for hit in hits}


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Kraftfahrer CE (m/w/d) – Auslieferung Bio-Lebensmittel", "Auslieferung in Berlin und Umland."),
        ("Fahrer m/w/d Paketzustellung C1 Pakete", "Wir suchen Fahrer für die Paketzustellung."),
        ("Fahrer (m/w/d) Nahverkehr", "Führerschein der Klasse C/CE mit eingetragener Ziffer 95 erforderlich."),
        ("Fahrer (m/w/d) Nahverkehr", "Sie besitzen die Fahrerlaubnis Klasse C1E."),
        ("Fahrer (m/w/d)", "Berufskraftfahrerqualifikation nach BKrFQG ist Voraussetzung."),
        ("Fahrer (m/w/d)", "Gültige Schlüsselzahl 95 im Führerschein."),
    ],
)
def test_b_profile_hides_vacancy_requiring_truck_class(title: str, body: str) -> None:
    verdict = _verdict(title, body)

    assert verdict.hard_reject
    assert _codes(verdict.rejection_hits) & {"driver_license_mismatch", "heavy_vehicle_mismatch"}
    assert all(hit.label_ru for hit in verdict.rejection_hits)


@pytest.mark.parametrize(
    "body",
    [
        "Kein C-Führerschein nötig, Klasse B reicht.",
        "Führerschein Klasse B reicht aus.",
        "Deutschkenntnisse C1 sind nicht erforderlich.",
    ],
)
def test_b_profile_keeps_vacancy_when_truck_class_is_negated(body: str) -> None:
    verdict = _verdict("Auslieferungsfahrer (m/w/d)", body)

    assert "driver_license_mismatch" not in _codes(verdict.rejection_hits)


def test_language_level_c1_in_title_is_not_a_license_class() -> None:
    signals = extract_license_requirement_signals(title="Kurierfahrer Deutsch C1", text="")

    assert signals.title_categories == ()


@pytest.mark.parametrize(
    "title",
    [
        "P - Scheinfahrer - Shuttle-Service - 15,50 € (m/w/d)",
        "Fahrer (m/w/d) mit P-Schein gesucht",
        "Fahrer für Personenbeförderung (m/w/d)",
        "Fahrer (m/w/d) Mietwagen mit Fahrer",
    ],
)
def test_passenger_transport_is_hidden_without_p_schein(title: str) -> None:
    verdict = _verdict(title, "Beförderung von Fahrgästen in Berlin.")

    assert "p_schein_required" in _codes(verdict.rejection_hits)


def test_passenger_transport_stays_with_p_schein_in_profile() -> None:
    verdict = _verdict("Fahrer (m/w/d) mit P-Schein gesucht", "Beförderung von Fahrgästen.", driver_license="B, P")

    assert "p_schein_required" not in _codes(verdict.rejection_hits)


@pytest.mark.parametrize("body", ["Auch ohne P-Schein möglich.", "Ein P-Schein ist nicht erforderlich."])
def test_waived_p_schein_does_not_hide(body: str) -> None:
    verdict = _verdict("Kurierfahrer (m/w/d)", body)

    assert "p_schein_required" not in _codes(verdict.rejection_hits)


def test_optional_p_schein_is_a_risk_not_a_rejection() -> None:
    verdict = _verdict("Kurierfahrer (m/w/d)", "Ein P-Schein ist von Vorteil.")

    assert "p_schein_required" not in _codes(verdict.rejection_hits)
    assert "p_schein_optional" in _codes(verdict.risk_hits)


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Fahrer (m/w/d) im qualifizierten Krankentransport", "Transport von Patienten."),
        ("Fahrer/in Krankentransport (m/w/d)", "Patientenfahrten in Berlin."),
        ("Fahrer (m/w/d)", "Ausbildung zum Rettungssanitäter wird vorausgesetzt."),
    ],
)
def test_qualified_medical_transport_is_hidden(title: str, body: str) -> None:
    verdict = _verdict(title, body)

    assert "medical_transport_required" in _codes(verdict.rejection_hits)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("Touren im Fernverkehr, Übernachtung im Fahrzeug.", "license_risk_long_haul"),
        ("Bedienung eines Gabelstaplers, Staplerschein von Vorteil.", "license_risk_forklift"),
        ("Fahrzeug mit Ladekran, Einweisung erfolgt.", "license_risk_loading_crane"),
    ],
)
def test_risk_markers_do_not_hide(body: str, code: str) -> None:
    verdict = _verdict("Auslieferungsfahrer Klasse B (m/w/d)", body)

    assert not verdict.hard_reject
    assert code in _codes(verdict.risk_hits)


def test_long_haul_is_not_a_risk_for_a_long_haul_profile() -> None:
    verdict = _verdict(
        "Sprinterfahrer Klasse B (m/w/d)", "Touren im Fernverkehr.", desired_roles=("Driver B – Fernverkehr",)
    )

    assert "license_risk_long_haul" not in _codes(verdict.risk_hits)


def test_profile_license_classes_parse_p_schein() -> None:
    assert parse_license_classes("B, BE") == ("B", "BE")
    assert parse_license_classes("B, P-Schein") == ("B", "P")
    assert parse_license_classes("FzF") == ("P",)
    assert parse_license_classes(None) == ()


@pytest.mark.parametrize(
    "fragment",
    [
        "Kraftfahrer CE",
        "Paketzustellung C1 Pakete",
        "P - Scheinfahrer - Shuttle",
        "Fahrer (m/w/d) mit P-Schein",
        "qualifizierten Krankentransport",
    ],
)
def test_real_run_hides_license_gated_vacancies(fragment: str) -> None:
    result = courier_run()

    assert not items_titled(visible_items(result), fragment), fragment
    assert hidden_reasons(result, fragment), fragment
