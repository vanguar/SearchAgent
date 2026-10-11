"""Складской прогон 11.10.2026: агентства, чужие роли, дубли, допуски к технике."""
from __future__ import annotations

import pytest
from app.services.deduper import _companies_match
from app.services.employer_identity import employer_key
from app.services.filter_engine import FilterEngine
from app.services.hashers import normalize_text_for_fingerprint
from app.services.license_requirement_signals import extract_license_requirement_signals
from app.services.role_family import RoleFamily, classify_vacancy_de
from app.services.source_adapters.adzuna_adapter import _effective_title
from app.services.staffing_agency import agency_brand_key, staffing_agency_evidence
from tests.services.relevance_support import build_canonical, courier_profile


def _warehouse_profile(**overrides: object):
    values: dict[str, object] = {
        "desired_roles": ("Lagerarbeiter", "Lagermitarbeiter"),
        "excluded_roles": (),
        "search_query_terms": (),
        "run_query_terms": (),
        "driver_license": None,
    }
    values.update(overrides)
    return courier_profile(**values)


# --- 1. Кадровые агентства без отраслевых слов в названии ---

@pytest.mark.parametrize(
    "company",
    [
        "Randstad",
        "Randstad Deutschland",
        "MANPOWER GmbH & Co. KG",
        "Adecco",
        "Orizon GmbH, NL Berlin-Brandenburg",
        "persona service AG & Co. KG",
        "DIS AG",
        "Hofmann Personal",
        "I. K. Hofmann",
        "Trenkwalder Group",
        "NEO Temp GmbH",
        "SYNERGIE Personal Solutions GmbH Karlsruhe",
        "KÖTTER Personal Service SE & Co. KG NL Berlin",
        "Arena Personal Mgmt. GmbH",
        "HANSE PERSONAL SERVICE GmbH",
        "Franke + Pahl Fachkräfte",
        "E & S Fachpersonal GmbH",
        "jobtimum GmbH",
        "DEKRA Arbeit GmbH",
    ],
)
def test_agency_brand_is_recognised(company: str) -> None:
    assert staffing_agency_evidence(build_canonical(title="Lagerhelfer", body="Lager.", company=company))


@pytest.mark.parametrize(
    "company",
    [
        "Bezirksamt Marzahn-Hellersdorf, Serviceeinheit Personal",
        "IKEA Deutschland GmbH & Co. KG",
        "Deutsche Post AG",
        "Dekra",
        "Personalabteilung Muster GmbH",
    ],
)
def test_ordinary_employer_is_not_an_agency(company: str) -> None:
    assert staffing_agency_evidence(build_canonical(title="Lagerhelfer", body="Lager.", company=company)) is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Randstad", "Randstad Deutschland"),
        ("persona service AG & Co.KG Berlin Wedding", "persona service AG & Co. KG Niederlassung Rostock"),
        ("Orizon GmbH, NL Berlin-Brandenburg", "orizon niederlassung rostock"),
        ("Unique Personalservice GmbH NL Berlin Mitte (O)", "Unique Personalservice GmbH"),
    ],
)
def test_agency_branches_share_one_brand_key(left: str, right: str) -> None:
    assert agency_brand_key(employer_key(left)) == agency_brand_key(employer_key(right))


def test_place_inside_agency_name_is_not_a_branch() -> None:
    # Pahl — ещё и деревня в Баварии, но здесь это часть названия фирмы.
    assert agency_brand_key(employer_key("Franke + Pahl Fachkräfte")) == "franke pahl fachkrafte"


# --- 2. Склад упомянут только как прошлый опыт кандидата ---

@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Quereinsteiger (m/w/d) Fahrkartenkontrolleur", RoleFamily.SECURITY),
        ("Sicherheitskraft Zugangskontrolle (m/w/d) Quereinsteiger Willkommen", RoleFamily.SECURITY),
        ("Verkaufs-Aushilfe (m/w/d) für Mode", RoleFamily.SALES),
        ("Verkaufsfahrer (m/w/d)", RoleFamily.DRIVING),
        ("Lagermitarbeiter / Sicherheitskraft (m/w/d)", RoleFamily.WAREHOUSE),
    ],
)
def test_title_family(title: str, family: RoleFamily) -> None:
    assert classify_vacancy_de(normalize_text_for_fingerprint(title)) is family


@pytest.mark.parametrize(
    "title",
    [
        "Quereinsteiger (m/w/d) Fahrkartenkontrolleur",
        "Sicherheitskraft Zugangskontrolle (m/w/d) Quereinsteiger Willkommen",
        "Verkaufs-Aushilfe (m/w/d) für Mode",
    ],
)
def test_warehouse_profile_hides_other_role_that_mentions_warehouse(title: str) -> None:
    body = (
        "Quereinsteiger aus Lager, Lagerlogistik oder Gastronomie willkommen. "
        "Erfahrung im Lager oder als Lagerhelfer ist hilfreich. Ware annehmen."
    )
    verdict = FilterEngine().evaluate(build_canonical(title=title, body=body), _warehouse_profile())

    assert verdict.hard_reject
    assert "profession_family_mismatch" in {hit.code for hit in verdict.rejection_hits}


# --- 3. Дубли: имя фирмы с пробелом и мусорный заголовок Adzuna ---

def test_spaced_initials_are_the_same_employer() -> None:
    assert employer_key("H 2 Personalmanagement GmbH") == employer_key("H2 Personalmanagement GmbH")
    assert _companies_match("h 2 personalmanagement gmbh", "h2 personalmanagement gmbh")
    assert employer_key("Deutsche Post AG") != employer_key("H2 Personalmanagement GmbH")


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        (
            "Das Wird Sie Überzeugen",
            "Lagermitarbeiter (m/w/d) mit Stapler- und Kranberechtigung Für unseren Kunden suchen wir … "
            "Das wird Sie überzeugen: unbefristeter Vertrag.",
            "Lagermitarbeiter (m/w/d) mit Stapler- und Kranberechtigung",
        ),
        (
            "Starte Deine Zukunft mit uns",
            "Lagermitarbeiter (m/w/d) 35 Stunden pro Woche • unbefristet Starte Deine Zukunft mit uns Die Firma …",
            "Lagermitarbeiter (m/w/d)",
        ),
        ("Lagermitarbeiter%28m%2Fw%2Fd%29", "Wir suchen Verstärkung.", "Lagermitarbeiter(m/w/d)"),
        # Нормальные заголовки не трогаются.
        ("Lagerhelfer", "Lagerhelfer (m/w/d) gesucht in Berlin.", "Lagerhelfer"),
        ("Fahrer (m/w/d)", "Picnic sucht Fahrer (m/w/d) in Teilzeit!", "Fahrer (m/w/d)"),
        # Подзаголовок, которого нет в тексте, — не подзаголовок раздела: оставить.
        ("Starte Deine Zukunft", "Lagermitarbeiter (m/w/d) gesucht.", "Starte Deine Zukunft"),
        # Текст начинается не с роли — заменять нечем.
        ("Das bieten wir", "Über uns (m/w/d) … Das bieten wir: Geld.", "Das bieten wir"),
    ],
)
def test_adzuna_effective_title(title: str, description: str, expected: str) -> None:
    assert _effective_title(title, description) == expected


# --- 4. Допуски к складской технике ---

@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("Lagermitarbeiter mit Staplerschein (m/w/d)", "", (("forklift", "required"),)),
        ("Gabelstaplerfahrer (m/w/d)", "", (("forklift", "required"),)),
        (
            "Lagermitarbeiter (m/w/d) mit Stapler- und Kranberechtigung", "",
            (("forklift", "required"), ("crane", "required")),
        ),
        ("Lagerhelfer", "Gabelstaplerschein erforderlich.", (("forklift", "required"),)),
        ("Lagerhelfer", "Ein Staplerschein ist von Vorteil.", (("forklift", "optional"),)),
        ("Lagerhelfer", "Wir bieten dir die Möglichkeit, einen Staplerschein zu machen.", (("forklift", "optional"),)),
        ("Lagerhelfer", "Wir unterstützen dich beim Erwerb des Staplerscheins.", (("forklift", "optional"),)),
        (
            "Lagerhelfer", "Wünschenswerterweise im Besitz des Staplerscheins, nicht Bedingung.",
            (("forklift", "optional"),),
        ),
        ("Lagerhelfer", "Du hast noch keinen Staplerschein? Kein Problem.", ()),
        ("Lagerhelfer", "Arbeit mit Stapler und Hubwagen.", ()),
        ("Lagermitarbeiter (m/w/d) Staplerschein optional", "", (("forklift", "optional"),)),
    ],
)
def test_equipment_permits(title: str, body: str, expected: tuple[tuple[str, str], ...]) -> None:
    signals = extract_license_requirement_signals(title=normalize_text_for_fingerprint(title), text=body)
    assert signals.equipment_permits == expected


def _risk_codes(title: str, body: str, profile) -> set[str]:
    verdict = FilterEngine().evaluate(build_canonical(title=title, body=body), profile)
    return {hit.code for hit in verdict.risk_hits}


def test_warehouse_profile_sees_permit_risks_without_hiding() -> None:
    profile = _warehouse_profile()
    verdict = FilterEngine().evaluate(
        build_canonical(title="Lagermitarbeiter mit Staplerschein (m/w/d)", body="Lagerarbeit in Berlin."), profile
    )

    assert not verdict.hard_reject
    assert "permit_forklift_required" in {hit.code for hit in verdict.risk_hits}
    assert "permit_crane_required" in _risk_codes("Lagermitarbeiter mit Kranschein (m/w/d)", "Lager.", profile)


def test_warehouse_profile_sees_truck_licence_risk_unless_optional() -> None:
    profile = _warehouse_profile()

    assert "permit_truck_license" in _risk_codes("Lagermitarbeiter (m/w/d) mit FS Kl. CE", "Lager.", profile)
    assert "permit_truck_license" not in _risk_codes(
        "Lagermitarbeiter (m/w/d) | Optional mit Fahrerlaubnis C/CE", "Lager.", profile
    )
    assert "permit_truck_license" not in _risk_codes(
        "Lagermitarbeiter (m/w/d) mit FS Kl. CE", "Lager.", _warehouse_profile(driver_license="CE")
    )


def test_driving_profile_keeps_its_own_licence_rules() -> None:
    codes = _risk_codes("Fahrer (m/w/d) mit Staplerschein", "Auslieferung in Berlin.", courier_profile())

    assert not any(code.startswith("permit_") for code in codes)
