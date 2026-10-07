"""Проблема 7: ложный риск «есть вопросы по допуску к работе»."""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine
from app.services.work_authorization_signals import (
    extract_work_authorization_requirements,
    unmet_work_authorization,
)
from tests.services.relevance_support import build_canonical, courier_profile, courier_run, items_titled, visible_items

# Дословно из объявления Deutsche Post «Paketzusteller Abrufkraft … mechZB Berlin-Britz».
_DHL_MINIJOB = (
    "Abrufkraft in der mechZB Berlin-Britz (m/w/d) Hinweis: „Keine Einschränkungen innerhalb der "
    "Arbeitserlaubnis. Beschäftigung nur möglich mit mehr als 20 Stunden/ Woche und mehr als 140 Tage im Jahr“ "
    "Notice: „No restrictions apply to the work permit. Employment is only possible with more than 20 hours/week."
)


def _review_codes(title: str, body: str, **overrides: object) -> dict[str, str]:
    verdict = FilterEngine().evaluate(build_canonical(title=title, body=body), courier_profile(**overrides))
    return {hit.code: hit.label_ru for hit in verdict.review_hits}


def test_minijob_condition_is_not_a_work_authorization_requirement() -> None:
    assert extract_work_authorization_requirements(_DHL_MINIJOB) == ()
    assert "sponsorship_review" not in _review_codes("Paketzusteller Abrufkraft (m/w/d)", _DHL_MINIJOB)


def test_work_permit_requirement_is_satisfied_by_section_24() -> None:
    body = "Eine gültige Arbeitserlaubnis ist erforderlich."

    assert "sponsorship_review" not in _review_codes("Paketzusteller (m/w/d)", body)
    assert "sponsorship_review" in _review_codes(
        "Paketzusteller (m/w/d)", body, work_authorized=False, legal_status=None
    )


@pytest.mark.parametrize(
    ("body", "label_part"),
    [
        ("Nur EU-Bürger können berücksichtigt werden.", "гражданство ЕС"),
        ("Voraussetzung: EU-Staatsbürgerschaft.", "гражданство ЕС"),
        ("Deutsche Staatsangehörigkeit erforderlich.", "немецкое гражданство"),
        ("Bereitschaft zur Sicherheitsüberprüfung nach SÜG.", "Sicherheitsüberprüfung"),
    ],
)
def test_citizenship_and_security_clearance_stay_risks_with_specific_label(body: str, label_part: str) -> None:
    codes = _review_codes("Kurierfahrer (m/w/d)", body)

    assert label_part in codes["sponsorship_review"]


def test_unmet_requirements_respect_profile_authorization() -> None:
    requirements = extract_work_authorization_requirements("Arbeitserlaubnis und EU-Bürger erforderlich.")

    assert {r.kind for r in unmet_work_authorization(requirements, work_authorized=True)} == {"eu_citizenship"}
    assert {r.kind for r in unmet_work_authorization(requirements, work_authorized=False)} == {
        "work_permit", "eu_citizenship",
    }


def test_real_run_dhl_minijobs_have_no_work_authorization_risk() -> None:
    cards = items_titled(visible_items(courier_run()), "Abrufkraft")

    assert cards
    for item in cards:
        assert "sponsorship_review" not in {hit.code for hit in item.filter_result.review_hits}
        assert "sponsorship_review" not in {hit.code for hit in item.score_result.negative_hits}
