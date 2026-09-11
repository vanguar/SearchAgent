"""Распознавание требования немецкого в разговорных формулировках.

Реальный случай: вакансия курьера DHL требовала «Du kannst dich auf Deutsch
unterhalten», а карточка показывала зелёную плашку «обязательный немецкий не указан»
как главный приоритет. Детектор ловил только существительное «Deutschkenntnisse»,
а низкопороговые объявления (DHL, Zeitarbeit, розница) почти всегда пишут на «ты»
глагольной формой — то есть промах приходился ровно на целевую категорию профиля.

Для пользователя без немецкого это ошибка в худшую сторону: он идёт на вакансию,
где язык обязателен.
"""

from __future__ import annotations

import pytest

from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.rule_catalog import inspect_vacancy
from app.services.search_models import SearchProfileContext
from app.services.source_adapters.models import SourceRecordPreview

_PROFILE = SearchProfileContext(
    profile_label="Доставка / Курьер",
    profile_source="saved",
    german_level="none",
    desired_roles=("курьер",),
    no_german_required=True,
)


def _signals(requirement_phrase: str):
    body = (
        "Werde Paketzusteller in 18198 Kritzmow. Was wir bieten 17,20 EUR Tarif-Stundenlohn, "
        "sofort in Vollzeit starten, flexible Einsatzmoeglichkeiten. "
        f"Was du als Zusteller bietest: Du darfst einen Pkw fahren. {requirement_phrase}."
    )
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id="careerjet",
            source_name="Careerjet",
            external_id="cj-1",
            source_reference="cj-1",
            title="Paketzusteller (m/w/d) in 18198 Kritzmow",
            company="DHL",
            location="Kritzmow",
            posted_at="2026-09-10",
            detail_url=None,
            raw_payload={"description": body},
        )
    )
    canonical = CanonicalVacancyGroup(
        canonical_key="k",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=("cj-1",),
    )
    return inspect_vacancy(canonical, _PROFILE)


@pytest.mark.parametrize(
    "phrase",
    [
        "Du kannst dich auf Deutsch unterhalten",   # формулировка из реальной вакансии
        "Du sprichst Deutsch",
        "Du sprichst gut Deutsch",
        "Sie sprechen Deutsch",
        "Du verständigst dich auf Deutsch",
        "Du beherrschst die deutsche Sprache",
        "Deutsch in Wort und Schrift",
        "Deutschkenntnisse erforderlich",
    ],
)
def test_german_requirement_is_detected(phrase: str) -> None:
    signals = _signals(phrase)

    assert signals.german_any_required is True
    # И, главное, зелёная плашка «не указан» не должна появиться.
    assert signals.no_mandatory_german_mentioned is False


@pytest.mark.parametrize(
    "phrase",
    [
        "Du musst kein Deutsch sprechen",
        "Deutschkenntnisse sind nicht erforderlich",
        "Keine Deutschkenntnisse notwendig",
        "Ohne Deutsch möglich",
    ],
)
def test_negated_german_is_not_read_as_a_requirement(phrase: str) -> None:
    signals = _signals(phrase)

    assert signals.german_any_required is False


def test_vacancy_without_language_talk_keeps_the_badge() -> None:
    """Плашка обязана оставаться там, где про язык действительно ничего не сказано."""
    signals = _signals("Du bist wetterfest und kannst gut anpacken")

    assert signals.german_any_required is False
    assert signals.no_mandatory_german_mentioned is True


@pytest.mark.parametrize(
    ("phrase", "expected_strong"),
    [
        ("Deutsch in Wort und Schrift", True),
        ("Du sprichst fließend Deutsch", True),
        ("Du kannst dich auf Deutsch unterhalten", False),
        ("Du sprichst Deutsch", False),
    ],
)
def test_conversational_german_is_not_reported_as_a_high_bar(phrase: str, expected_strong: bool) -> None:
    """Разговорный уровень — это требование, но не «нужен хороший немецкий»."""
    assert _signals(phrase).strong_german_required is expected_strong


# --- Утверждать ОТСУТСТВИЕ требования можно только по цельному тексту ---

def _signals_for_body(body: str):
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id="careerjet",
            source_name="Careerjet",
            external_id="cj-2",
            source_reference="cj-2",
            title="Postbote für Pakete und Briefe (m/w/d) in 18059 Rostock",
            company="DHL",
            location="Rostock",
            posted_at="2026-09-10",
            detail_url=None,
            raw_payload={"description": body},
        )
    )
    canonical = CanonicalVacancyGroup(
        canonical_key="k2",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=("cj-2",),
    )
    return inspect_vacancy(canonical, _PROFILE)


def test_excerpt_starting_mid_sentence_cannot_claim_the_requirement_is_absent() -> None:
    """Careerjet отдаёт фрагмент вокруг ключевого слова, а не начало объявления.

    У реальной вакансии почтальона строка «Du kannst dich auf Deutsch unterhalten»
    стояла ВЫШЕ начала вырезки: движок получил 651 символ с середины фразы и уверенно
    показывал «обязательный немецкий не указан».
    """
    signals = _signals_for_body(
        "und hängst dich rein Werde Postbote bei Deutsche Post DHL. Als Postbote bringst du "
        "den Menschen in deinem Bezirk Post- und Paketsendungen. Dabei lässt du dir von keinem "
        "Wetter die Laune verderben und bist fünf Werktage pro Woche unterwegs."
    )

    assert signals.no_mandatory_german_mentioned is False


def test_truncated_excerpt_cannot_claim_the_requirement_is_absent() -> None:
    signals = _signals_for_body(
        "Wir suchen DICH als Saisonkraft! Werde Paketzusteller in 18198 Kritzmow. Was wir bieten "
        "17,20 EUR Tarif-Stundenlohn, sofort in Vollzeit starten, flexible Einsatzmöglichkeiten. "
        "Deine Aufgaben als…"
    )

    assert signals.no_mandatory_german_mentioned is False


def test_complete_description_still_claims_the_requirement_is_absent() -> None:
    """Иначе мы просто убрали бы полезный сигнал везде."""
    signals = _signals_for_body(
        "Wir suchen Lagerhelfer für unser Lager in Rostock. Wir bieten geregelte Arbeitszeiten "
        "im Zwei-Schicht-System, pünktliche Bezahlung nach Tarif und eine bezahlte Einarbeitung. "
        "Der Einstieg ist kurzfristig möglich."
    )

    assert signals.no_mandatory_german_mentioned is True


def test_requirement_found_inside_an_excerpt_is_still_reported() -> None:
    """Найденное в куске текста остаётся найденным — ограничение только на вывод об отсутствии."""
    signals = _signals_for_body(
        "und hängst dich rein. Was du als Zusteller bietest: Du darfst einen Pkw fahren. "
        "Du kannst dich auf Deutsch unterhalten. Du bist wetterfest und kannst gut anpacken."
    )

    assert signals.german_any_required is True
