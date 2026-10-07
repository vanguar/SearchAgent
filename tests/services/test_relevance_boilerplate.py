"""Проблема 5: шаблонный текст работодателя не влияет на признаки и балл."""
from __future__ import annotations

import dataclasses

from app.services.employer_boilerplate import strip_employer_boilerplate
from app.services.rule_catalog import inspect_vacancy
from app.services.scorer import VacancyScorer
from tests.services.relevance_support import build_canonical, courier_profile, courier_run, items_titled

_TEMPLATE = (
    "Wir sind ein nach AZAV zertifizierter Personalvermittler.   "
    "Grundkenntnisse in Deutsch reichen völlig aus, Quereinsteiger sind herzlich willkommen bei uns.   "
    "Mit Ihrer Bewerbung willigen Sie in die Verarbeitung Ihrer personenbezogenen Daten ein."
)


def _agency_vacancy(index: int, specific: str):
    return build_canonical(
        title=f"Fahrer (m/w/d) Tour {index}",
        body=f"{specific}   {_TEMPLATE}",
        company="Muster Arbeitsvermittlung GmbH & Co. KG",
        external_id=f"agency-{index}",
    )


def test_paragraphs_repeated_in_most_vacancies_of_one_employer_are_removed() -> None:
    groups = [_agency_vacancy(index, f"Auslieferung von Möbeln im Bezirk {index}, Klasse B.") for index in range(4)]

    cleaned = strip_employer_boilerplate(groups)

    for group in cleaned:
        body = group.source_records[0].body_text
        assert "Grundkenntnisse" not in body
        assert "AZAV" not in body
        assert "Auslieferung von Möbeln" in body


def test_repeated_template_no_longer_creates_basic_german_signal() -> None:
    groups = [_agency_vacancy(index, f"Lieferfahrten für Kunden in Bezirk {index}.") for index in range(4)]
    profile = courier_profile()

    before = inspect_vacancy(groups[0], profile)
    after = inspect_vacancy(strip_employer_boilerplate(groups)[0], profile)

    assert before.basic_german_signal
    assert not after.basic_german_signal
    assert not after.low_language_signal


def test_requirement_paragraphs_are_never_stripped() -> None:
    template = "Führerschein Klasse CE ist zwingend erforderlich und wird bei allen Touren geprüft."
    groups = [
        build_canonical(title=f"Fahrer Tour {index}", body=f"Tour {index} im Nahverkehr.   {template}",
                        company="Spedition Muster GmbH", external_id=f"truck-{index}")
        for index in range(4)
    ]

    cleaned = strip_employer_boilerplate(groups)

    assert all("Klasse CE" in group.source_records[0].body_text for group in cleaned)


def test_single_vacancy_employer_keeps_its_text_except_service_markers() -> None:
    group = _agency_vacancy(1, "Lieferfahrten in Berlin.")

    body = strip_employer_boilerplate([group])[0].source_records[0].body_text

    assert "Grundkenntnisse in Deutsch" in body
    assert "AZAV" not in body
    assert "personenbezogenen Daten" not in body


def test_driving_basics_are_not_basic_german() -> None:
    signals = inspect_vacancy(
        build_canonical(title="Auslieferungsfahrer", body="Führerschein Klasse B und Grundkenntnisse als Fahrer."),
        courier_profile(),
    )

    assert not signals.basic_german_signal
    assert not signals.low_language_signal


def test_unconfirmed_language_relief_scores_below_confirmed_relief() -> None:
    profile = courier_profile()
    body = (
        "Wir suchen Auslieferungsfahrer für Getränke in Berlin. Keine Deutschkenntnisse erforderlich. "
        "Führerschein Klasse B, Arbeitszeit Montag bis Freitag, unbefristeter Vertrag, faire Bezahlung "
        "nach Tarif und ein kollegiales Team in unserem Lager in Spandau."
    )
    snippet = build_canonical(title="Auslieferungsfahrer (m/w/d)", body=body)
    record = dataclasses.replace(snippet.source_records[0], description_complete=True)
    confirmed = dataclasses.replace(snippet, source_records=(record,))

    snippet_score = VacancyScorer().score(snippet, profile)
    confirmed_score = VacancyScorer().score(confirmed, profile)

    assert inspect_vacancy(snippet, profile).low_language_signal
    assert snippet_score.score < confirmed_score.score


def test_real_run_event_fahrer_loses_template_language_bonus() -> None:
    result = courier_run()
    cards = items_titled((*result.hot_results, *result.maybe_results, *result.rejected_results), "Hochzeiten")
    hidden = items_titled(result.hidden_filtered_items, "Hochzeiten")

    assert cards or hidden
    for item in cards:
        codes = {hit.code for hit in item.score_result.positive_hits}
        assert "basic_german_signal" not in codes
        assert "low_language_signal" not in codes
        assert item.score_result.score < 99
