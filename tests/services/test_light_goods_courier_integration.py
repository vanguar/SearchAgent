"""Перевозка грузов на лёгком автомобиле после исправления курьерского шума.

Исправление курьерского профиля меняет общие правила: составные курьерские
заголовки, операторов техники и бонус за слова доставки из описания. Новая
специализация пользуется теми же правилами, поэтому её цели и её шум закреплены
здесь отдельно — вместе с узким правилом "Mitarbeiter Logistik", которое
подтверждает перевозку только обязанностями из описания.
"""
from datetime import datetime

import pytest
from app.services.filter_engine import explain_filter_decision
from scripts.diagnose_light_transport import PROFILE, group_records

VISIBLE = {"hot", "maybe"}


@pytest.fixture(autouse=True)
def freeze_scoring_date(monkeypatch):
    monkeypatch.setattr("app.services.scorer.utc_now", lambda: datetime(2026, 10, 2, 12, 0))


def _decision(title: str, body: str = "") -> str:
    record = {
        "source_id": "ba", "source_name": "BA", "external_id": "integration-1", "source_reference": None,
        "title": title, "company": "Fixture GmbH", "location": "Berlin", "posted_at": "2026-10-02",
        "detail_url": "https://example.org/integration/1", "raw_payload": {"description": body},
    }
    return explain_filter_decision(group_records([record])[0], PROFILE)["final_decision"]


@pytest.mark.parametrize(
    ("title", "body"),
    (
        ("Sprinterfahrer Klasse B", ""),
        ("Transporterfahrer bis 3,5 t", ""),
        ("Fahrer Klasse B Transporter", ""),
        ("Fahrer 3,5 t", ""),
        ("Fahrer bis 3,5 t", ""),
        ("Fahrer Klasse B im Nahverkehr", "Sie liefern täglich Waren an unsere Filialen. Grundkenntnisse Deutsch."),
        ("Fahrer Direktfahrten Klasse B", ""),
        ("Fahrer Sonderfahrten Transporter", ""),
        ("Kraftfahrer 3,5 t Klasse B", ""),
        ("Berufskraftfahrer 3,5 t Klasse B", ""),
    ),
)
def test_light_goods_targets_stay_visible(title: str, body: str) -> None:
    assert _decision(title, body) in VISIBLE, title


def test_logistics_worker_with_regular_van_duty_stays_reviewable() -> None:
    # Подавление бонуса из тела касается курьерского семейства, а не этого:
    # перевозка подтверждена одной фразой обязанностей, работа остаётся на проверке.
    body = "Sie transportieren täglich Waren mit einem Sprinter zwischen unseren drei Standorten."

    assert _decision("Mitarbeiter Logistik", body) == "maybe"


@pytest.mark.parametrize(
    ("title", "body"),
    (
        ("Kraftfahrer CE", "Führerschein Klasse CE zwingend erforderlich."),
        ("Berufskraftfahrer Fernverkehr", ""),
        ("LKW-Fahrer CE", ""),
        ("Müllfahrer", ""),
        ("Baggerfahrer", ""),
        ("Kranfahrer", ""),
        ("Busfahrer", ""),
        ("Taxifahrer", ""),
        ("Sicherheitskraft", "Egal, ob Sie als Lagermitarbeiter, Fahrer oder Kellner tätig waren."),
        ("Redakteur", "Im Medienhaus entstehen die Berliner Zeitung und der Berliner Kurier."),
        ("Paketfahrer (m/w/d)", "Zustellung von Paketen an Privatkunden."),
        ("Selbständiger Transporterfahrer mit Gewerbeschein", "Gewerbeanmeldung erforderlich."),
        ("Lagermitarbeiter", "Gelegentlich fahren Sie einen Transporter. Waren im Lager kommissionieren."),
    ),
)
def test_light_goods_noise_stays_hidden(title: str, body: str) -> None:
    assert _decision(title, body) not in VISIBLE, title
