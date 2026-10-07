"""Проблема 6: нормализация работодателя и заголовка, склейка дублей."""
from __future__ import annotations

import pytest
from app.services.employer_identity import employer_key
from app.services.normalizer import VacancyNormalizer
from app.services.source_adapters.models import SourceRecordPreview
from app.services.source_merge import SourceMergeService
from app.services.title_normalizer import TitleNormalizer
from tests.services.relevance_support import courier_run, items_titled, visible_items


@pytest.mark.parametrize(
    "name",
    [
        "Deutsche Post AG",
        "Deutsche Post",
        "DHL",
        "Deutsche Post DHL",
        "Deutsche Post & DHL",
        "Deutsche Post und DHL - Niederlassung Betrieb Berlin 1",
    ],
)
def test_deutsche_post_spellings_share_one_employer_key(name: str) -> None:
    assert employer_key(name) == employer_key("Deutsche Post AG")


@pytest.mark.parametrize(
    "name",
    ["perZukunft", "PerZukunft Arbeitsvermittlung GmbH & Co. KG", "PerZukunft Arbeitsvermittlung GmbH&Co.KG"],
)
def test_agency_legal_forms_share_one_employer_key(name: str) -> None:
    assert employer_key(name) == "perzukunft"


def test_different_employers_keep_different_keys() -> None:
    assert employer_key("Picnic GmbH") != employer_key("Coolblue Deutschland GmbH")
    assert employer_key(None) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fahrer (m/w/d) mit Ladetätigkeit", "fahrer mit ladetatigkeit"),
        ("Fahrer:in / Reiniger:in Carsharing (w/m/x/d)", "fahrer reiniger carsharing"),
        ("Kurierfahrer (gn)", "kurierfahrer"),
        ("Pizzafahrer*in", "pizzafahrer"),
        ("Mitarbeiter/in  Lager", "mitarbeiter lager"),
        ("Fahrer m/w/d Paketzustellung", "fahrer paketzustellung"),
        ("Klasse C/CE Fahrer", "klasse c ce fahrer"),
    ],
)
def test_title_normalization_strips_gender_markers_only(raw: str, expected: str) -> None:
    assert TitleNormalizer().normalize(raw) == expected


def _record(external_id: str, title: str, body: str, *, company: str = "Picnic", location: str = "Berlin"):
    return VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id="adzuna", source_name="Adzuna", external_id=external_id, source_reference=external_id,
            title=title, company=company, location=location, posted_at="2026-10-01",
            detail_url=f"https://example.org/{external_id}", raw_payload={"description": body},
        )
    )


_PICNIC_BODY = (
    "Als Fahrer bei Picnic lieferst du Lebensmittel mit unseren elektrischen Transportern an unsere Kunden aus. "
    "Du brauchst einen Führerschein der Klasse B, Erfahrung ist nicht nötig. Wir bieten einen unbefristeten "
    "Vertrag, feste Schichten und eine gründliche Einarbeitung im Hub Marienfelde."
)


def test_same_text_under_different_role_titles_is_one_card() -> None:
    titles = ("Fahrer", "Kurier", "Kurierfahrer", "Lieferfahrer", "Auslieferungsfahrer")
    records = tuple(
        _record(f"picnic-{index}", f"{title} (m/w/d) - Berlin - Marienfelde", _PICNIC_BODY)
        for index, title in enumerate(titles)
    )

    groups = SourceMergeService().merge_records(records).canonical_groups

    assert len(groups) == 1
    assert len(groups[0].source_records) == len(titles)


def test_same_text_in_another_district_stays_separate() -> None:
    records = (
        _record("picnic-m", "Fahrer (m/w/d) - Berlin - Marienfelde", _PICNIC_BODY),
        _record("picnic-t", "Fahrer (m/w/d) - Berlin - Tegel", _PICNIC_BODY),
    )

    assert len(SourceMergeService().merge_records(records).canonical_groups) == 2


def test_employer_spelling_variants_with_same_title_and_text_are_one_card() -> None:
    body = "Werde Postbote für Pakete und Briefe in Berlin-Tempelhof. 17,92 € Tarif-Stundenlohn, Vollzeit. " * 3
    records = (
        _record("dp-1", "Postbote für Briefe und Pakete in Berlin-Tempelhof (m/w/d)", body,
                company="Deutsche Post AG", location="Mitte, Berlin"),
        _record("dp-2", "Postbote für Briefe und Pakete in Berlin-Tempelhof (m/w/d)", body,
                company="Deutsche Post und DHL - Niederlassung Betrieb Berlin 1", location="Berlin, Deutschland"),
    )

    assert len(SourceMergeService().merge_records(records).canonical_groups) == 1


def test_real_run_collapses_picnic_and_timepartner_duplicates() -> None:
    visible = visible_items(courier_run())

    marienfelde = [
        item for item in items_titled(visible, "Marienfelde", company="Picnic")
        if "Lieferant" not in item.primary_record.original_title
    ]
    assert len(marienfelde) <= 1
    assert len(items_titled(visible, "Fahrer mit Ladetätigkeit im Nahverkehr")) <= 1


@pytest.mark.parametrize("district", ["Tempelhof", "Grünau"])
def test_real_run_shows_one_deutsche_post_postbote_card_per_district(district: str) -> None:
    cards = [
        item for item in items_titled(visible_items(courier_run()), f"Postbote für Briefe und Pakete in Berlin-{district}")
        if employer_key(item.primary_record.original_company) == employer_key("DHL")
    ]

    assert len(cards) == 1
