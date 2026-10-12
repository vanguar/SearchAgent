from dataclasses import replace

import pytest
from app.services.profile_form_spec import parse_profile_form
from app.services.search_models import SearchProfileContext
from app.services.warehouse_transport import WAREHOUSE_ROUTE_ROLE, warehouse_route_hits

PROFILE = SearchProfileContext(
    profile_label="Mixed strict", profile_source="saved", strict_match_mode="a1",
    desired_roles=(WAREHOUSE_ROUTE_ROLE, "Lagerhelfer", "Reinigungskraft"),
)


@pytest.mark.parametrize("body", [
    "Sie transportieren Waren mit einem Sprinter zwischen unseren Lagern und Filialen. Führerschein B.",
    "Sie fahren Waren im Transporter auf Direktfahrten im Fernverkehr. Führerschein Klasse B.",
    "Belieferung unserer Filialen mit Waren im Sprinter. Führerschein Klasse B.",
    "Sie transportieren Waren mit einem Sprinter zwischen unseren Lagern. Führerschein B. Keine Paketzustellung.",
])
def test_goods_routes_pass(body):
    assert warehouse_route_hits("Fahrer Klasse B", body, PROFILE) == ((), ())


@pytest.mark.parametrize("title,body", [
    ("Sprinterfahrer Klasse B", "Auslieferung von Waren an Privatkunden bis zur Wohnungstür."),
    ("Paketzusteller", "Sprinter, Führerschein B. Abfahrt vom Lager, 150 Pakete pro Tag."),
    ("Fahrer Fahrzeugpfleger", "Klasse B. Reinigen und Umstellen unserer Fahrzeuge."),
    ("Fahrer", "Führerschein Klasse B. Transport von Patienten mit dem Sprinter."),
    ("Auslieferungsfahrer", "Führerschein B. Sie liefern Waren. Strecke und Empfänger nicht genannt."),
    ("Delivery Driver", "Paketzustellung mit Sprinter Klasse B."),
    ("Fahrer Klasse B", "Sprinter Warenverkehr, aber keine Direktfahrten und kein Werksverkehr."),
])
def test_last_mile_and_unconfirmed_routes_are_excluded(title, body):
    assert warehouse_route_hits(title, body, PROFILE)[0]


def test_standalone_warehouse_work_remains_eligible():
    assert warehouse_route_hits("Lagerhelfer", "Pakete sortieren.", PROFILE) == ((), ())


def test_ordinary_and_existing_driver_profiles_are_unchanged():
    for profile in [replace(PROFILE, strict_match_mode="off"), replace(PROFILE, desired_roles=("Kurierfahrer",))]:
        assert warehouse_route_hits("Paketzusteller", "Sprinter Klasse B. Paketzustellung.", profile) == ((), ())


def test_business_recipients_need_route_confirmation():
    body = "Du lieferst unsere Produkte mit dem Kühl-Sprinter an unsere Partner in Berlin und anderen Städten. Klasse B."
    rejected, review = warehouse_route_hits("Fahrer Kühltransport", body, PROFILE)
    assert not rejected
    assert {h.code for h in review} == {"strict_route_review"}


def test_business_recipients_cannot_rescue_private_deliveries():
    body = "Du lieferst unsere Produkte mit dem Sprinter an unsere Partner und Privatkunden. Klasse B."
    assert {h.code for h in warehouse_route_hits("Fahrer", body, PROFILE)[0]} == {"strict_last_mile"}


def test_route_role_survives_profile_form_round_trip():
    fields = parse_profile_form({"primary_role": WAREHOUSE_ROUTE_ROLE, "additional_roles": "Lagerhelfer, Reinigungskraft"})
    assert fields.desired_roles == PROFILE.desired_roles
