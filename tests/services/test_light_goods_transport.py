"""Precision and compatibility contract for light goods transport (no saved profiles)."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from app.services.filter_engine import FilterEngine
from app.services.hashers import normalize_text_for_fingerprint
from app.services.role_family import RoleFamily, classify_role_text, classify_vacancy_de
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import inspect_vacancy
from scripts.diagnose_light_transport import PROFILE, group_records

CASES = json.loads((Path(__file__).parents[1] / "fixtures/light_transport_frozen.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["record"]["title"] for c in CASES])
def test_frozen_semantic_contract(case):
    group = group_records([case["record"]])[0]
    result = FilterEngine().evaluate(group, PROFILE)
    if case["label"] == "GOOD":
        assert not result.hard_reject, result.rejection_hits
    else:
        assert result.hard_reject  # No light + goods evidence: not this specialization.


def evaluate(title, body="", profile=PROFILE):
    record = {**CASES[0]["record"], "title": title, "raw_payload": {"description": body}}
    group = group_records([record])[0]
    signals = inspect_vacancy(group, profile)
    return signals, FilterEngine().evaluate(group, profile, signals=signals)


@pytest.mark.parametrize("title", [
    "Sprinter-Fahrer", "Sprinter Fahrer", "Transporter-Fahrer", "Transporter Fahrer",
    "Fahrer für Transporter", "Fahrer bis 3,5t", "Fahrer 3,5-Tonner",
    "Kleintransporter Fahrer", "Kurierfahrer Sonderfahrten Klasse B",
])
def test_spelling_variants(title):
    assert classify_role_text(title) == RoleFamily.LIGHT_GOODS_TRANSPORT
    assert normalize_role_intent(title).family == RoleFamily.LIGHT_GOODS_TRANSPORT
    assert not evaluate(title)[1].hard_reject


@pytest.mark.parametrize("title,body", [
    ("Sprinterfahrer", "Sie transportieren Patienten im Krankenfahrdienst."),
    ("Transporterfahrer", "Paketzustellung für Amazon DSP. 180 Pakete pro Tag."),
    ("Fahrer Klasse B im Werksverkehr", "Überführen der Fahrzeuge von A nach B."),
    ("Sicherheitskraft Klasse B", "Sie fahren täglich Waren mit dem Sprinter zwischen Standorten."),
    ("Lagermitarbeiter", "Gelegentlich fahren Sie Waren mit dem Sprinter."),
    ("Redakteur Sprinterfahrer", "Wir berichten über Warentransport."),
    ("Fahrer Nahverkehr Klasse B", "Sie befördern Personen mit dem Kleinbus."),
    ("Servicefahrer Klasse B", "Verkauf und Beratung stehen im Mittelpunkt. Eigener PKW."),
    ("Sprinterfahrer", "Führerschein Klasse CE zwingend erforderlich."),
    ("Transporterfahrer", "Sie fahren einen 7,5 Tonner."),
])
def test_contrary_duties_and_titles_win(title, body):
    signals, result = evaluate(title, body)
    assert result.hard_reject
    if "Sprinterfahrer" not in title and "Transporterfahrer" not in title:
        assert not signals.positive_role_hits_in_title


@pytest.mark.parametrize("language", ["Grundkenntnisse Deutsch", "Deutsch von Vorteil", "einfache Deutschkenntnisse", ""])
def test_language_is_not_an_invented_barrier(language):
    assert not evaluate("Sprinterfahrer Klasse B", language)[1].hard_reject


@pytest.mark.parametrize("body", [
    "Nur selbständige Fahrer mit eigenem Gewerbe gesucht.",
    "Subunternehmer mit Gewerbeschein gesucht.",
])
def test_existing_self_employment_filter(body):
    result = evaluate("Transporterfahrer", body)[1]
    assert any(hit.code == "self_employment_mismatch" for hit in result.rejection_hits)


def test_duties_can_support_generic_logistics_title_but_require_review():
    signals, result = evaluate("Mitarbeiter Logistik", "Sie transportieren täglich Waren mit einem Sprinter zwischen unseren drei Standorten.")
    assert result.review_required
    assert signals.positive_role_hits
    assert not signals.positive_role_hits_in_title


@pytest.mark.parametrize("role", ["Курьер", "Fahrer B"])
def test_existing_driving_profiles_still_accept_vans(role):
    assert not evaluate("Sprinterfahrer Klasse B", profile=replace(PROFILE, desired_roles=(role,), search_query_terms=()))[1].hard_reject


def test_taxonomy_keeps_parcels_transfers_and_warehouse_separate():
    for title, expected in [("Paketzusteller", RoleFamily.DRIVING), ("Fahrzeugüberführer", RoleFamily.VEHICLE_LOGISTICS),
                            ("Staplerfahrer", RoleFamily.WAREHOUSE)]:
        assert classify_vacancy_de(normalize_text_for_fingerprint(title)) == expected


@pytest.mark.parametrize("category", ["C", "CE", "C1", "C1E"])
def test_required_heavy_classes_win_over_van(category):
    assert evaluate("Sprinterfahrer Klasse B", f"Führerschein Klasse {category} zwingend erforderlich.")[1].hard_reject


@pytest.mark.parametrize("body", ["Führerschein Klasse B. Klasse CE von Vorteil.", "Klasse CE nicht erforderlich.", "Führerschein Klasse B oder C."])
def test_nonmandatory_heavy_classes_do_not_hide_light_goods(body):
    assert not evaluate("Sprinterfahrer", body)[1].hard_reject


@pytest.mark.parametrize("title,body", [
    ("Sprinterfahrer", "Feste Touren statt reiner Paketzustellung."),
    ("Auslieferungsfahrer (nicht Verkaufsfahrer) Klasse B", "Transport von Waren mit einem Sprinter."),
    ("Auslieferungsfahrer 3,5t LKW", "Transport von Waren mit Transporter bis 3,5 t. Führerschein B."),
])
def test_negations_and_explicit_light_mass_preserve_real_jobs(title, body):
    assert not evaluate(title, body)[1].hard_reject


@pytest.mark.parametrize("title", ["Servicefahrer Transporter", "Shuttlefahrer Sprinter", "Fahrer Werksverkehr Klasse B"])
def test_vehicle_does_not_prove_goods_for_ambiguous_services(title):
    assert evaluate(title)[1].hard_reject


@pytest.mark.parametrize("body", [
    "Selbstständige und sorgfältige Arbeitsweise. Vollzeit.",
    "Du bist zuverlässig, pünktlich und arbeitest selbstständig. Teilzeit.",
    "Keine 100+ Stopps pro Tag wie im klassischen Paketdienst. Feste Warentransporttouren.",
    "Führerschein Klasse 2 oder C1 inkl. der Module des BKrFQG (95) ist von Vorteil. Festanstellung.",
])
def test_live_light_transport_phrases(body):
    assert not evaluate("Auslieferungsfahrer 3,5t LKW", body)[1].hard_reject


def test_work_style_does_not_mask_real_self_employment_requirement():
    assert evaluate("Sprinterfahrer", "Selbstständige Arbeitsweise. Eigenes Gewerbe erforderlich.")[1].hard_reject


def test_optional_requirement_does_not_mask_mandatory_heavy_license():
    assert evaluate("Sprinterfahrer 3,5t", "Klasse CE zwingend erforderlich, Erfahrung von Vorteil.")[1].hard_reject


def test_optional_language_in_next_sentence_does_not_waive_ce():
    assert evaluate("Sprinterfahrer 3,5t", "Führerschein CE. Deutsch von Vorteil.")[1].hard_reject


def test_profile_label_cannot_override_an_office_profession():
    assert classify_role_text("Sachbearbeiter Transporter Klasse B") != RoleFamily.LIGHT_GOODS_TRANSPORT
