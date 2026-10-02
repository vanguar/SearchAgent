"""Курьерский профиль: откуда берётся сигнал «роль в доставке».

Замороженный корпус run 1354 показал две стороны одного дефекта. Составные
курьерские заголовки ("Paketzusteller", "Postbote") не давали сигнала в
заголовке и держались на словах из тела, а чужие профессии получали тот же
бонус из тела: "Berliner Kurier" в подписи издательства, "Fahrer" в списке
профессий для Quereinsteiger, "Baggerfahrer" через общий хвост "-fahrer".
"""
from datetime import date

import pytest
from app.services.filter_engine import FilterEngine
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.role_family import DRIVING_LIKE_FAMILIES, classify_vacancy_de
from app.services.rule_catalog import inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_service import _assign_bucket
from app.services.source_adapters.models import SourceRecordPreview

FIXTURE_TODAY = date(2026, 4, 24)
NEUTRAL_BODY = "Wir suchen Verstärkung für unser Team in Berlin. Vollzeit, unbefristet."


def _profile(**overrides: object) -> SearchProfileContext:
    payload = {
        "profile_label": "Доставка / Курьер",
        "profile_source": "saved",
        "note_ru": "Используется сохраненный профиль поиска.",
        "legal_status": "Section 24",
        "work_authorized": True,
        "german_level": "basic",
        "desired_roles": ("Доставка", "Курьер", "Водитель"),
        "excluded_roles": (),
        "preferred_locations": (),
        "relocation_ready": True,
        "shift_ok": True,
        "physical_work_ok": True,
        "driver_license": "B",
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


def _canonical(title: str, body: str = NEUTRAL_BODY) -> CanonicalVacancyGroup:
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            description_complete=True,
            source_id="careerjet",
            source_name="Careerjet",
            external_id="fixture-1",
            source_reference="fixture-1",
            title=title,
            company="Nord Team GmbH",
            location="Berlin, Deutschland",
            posted_at="2026-04-16",
            detail_url="https://example.org/jobs/fixture-1",
            raw_payload={"description": body},
        )
    )
    return CanonicalVacancyGroup(
        canonical_key="canonical-fixture",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=(record.source_record_key,),
    )


def _score(title: str, body: str = NEUTRAL_BODY, profile: SearchProfileContext | None = None):
    profile = profile or _profile()
    canonical = _canonical(title, body)
    filter_engine = FilterEngine()
    filter_result = filter_engine.evaluate(canonical, profile)
    score_result = VacancyScorer(filter_engine=filter_engine).score(
        canonical, profile, filter_result=filter_result, today=FIXTURE_TODAY
    )
    return (
        score_result,
        _assign_bucket(filter_result=filter_result, score=score_result.score),
        filter_result,
    )


@pytest.mark.parametrize(
    "title",
    (
        "Paketzusteller (m/w/d)",
        "Briefzusteller (m/w/d) - Vollzeit",
        "Postbote für Briefe und Pakete (m/w/d)",
        "Expresskurier (m/w/d)",
        "Auslieferungsfahrer (m/w/d)",
        "Fahrer für den Kurierdienst (m/w/d)",
    ),
)
def test_courier_title_gets_a_title_role_signal(title: str) -> None:
    signals = inspect_vacancy(_canonical(title), _profile())
    score_result, bucket, _ = _score(title)

    assert "delivery_driving_family" in {hit.code for hit in signals.positive_role_hits_in_title}, title
    assert "priority_role" in {hit.code for hit in score_result.positive_hits}, title
    assert bucket in ("hot", "maybe"), title


@pytest.mark.parametrize(
    "title",
    ("Baggerfahrer (m/w/d)", "Radladerfahrer (m/w/d)", "Kranfahrer (m/w/d)", "Maschinenfahrer (m/w/d)"),
)
def test_machine_operator_is_not_a_delivery_driver(title: str) -> None:
    canonical = _canonical(title)
    signals = inspect_vacancy(canonical, _profile())
    _, bucket, _ = _score(title)

    assert classify_vacancy_de(canonical.normalized_title) not in DRIVING_LIKE_FAMILIES, title
    assert "delivery_driving_family" not in {hit.code for hit in signals.positive_role_hits}, title
    assert bucket == "rejected", title


def test_construction_title_listing_a_digger_driver_is_not_courier_work() -> None:
    _, bucket, _ = _score(
        "Tiefbauer, Baggerfahrer, Straßenbauer (m/w/d)",
        "Wir suchen Sie als Tiefbauer oder Baggerfahrer oder Straßenbauer! Rohrleitungsarbeiten.",
    )

    assert bucket == "rejected"


def test_newspaper_name_in_publisher_footer_gives_no_delivery_bonus() -> None:
    body = (
        "Sicherer Umgang mit KI-Tools in Recherche und Textproduktion. "
        "Im Medienhaus des Berliner Verlags entstehen die Berliner Zeitung, "
        "der Berliner Kurier und die Ostdeutsche Allgemeine Zeitung."
    )
    signals = inspect_vacancy(_canonical("Redakteur Open Source (m/w/d)", body), _profile())
    score_result, bucket, _ = _score("Redakteur Open Source (m/w/d)", body)
    codes = {hit.code for hit in score_result.positive_hits}

    # Слово найдено, но только в теле: сигнал остаётся, бонуса за него нет.
    assert "delivery_driving_family" in {hit.code for hit in signals.positive_role_hits}
    assert not signals.positive_role_hits_in_title
    assert "related_role_mention" not in codes
    assert "desired_role_match" not in codes
    assert bucket == "rejected"


def test_driver_in_a_list_of_welcome_professions_gives_no_delivery_bonus() -> None:
    body = (
        "Quereinsteiger aus verschiedenen Berufen sind herzlich willkommen! "
        "Egal, ob Sie als Lagermitarbeiter, Fahrer, Kellner oder in anderen Bereichen tätig waren."
    )
    score_result, bucket, _ = _score("Sicherheitskraft Zugangskontrolle (m/w/d)", body)
    codes = {hit.code for hit in score_result.positive_hits}

    assert "related_role_mention" not in codes
    assert "desired_role_match" not in codes
    assert bucket == "rejected"


def test_body_only_bonus_is_kept_for_other_role_families() -> None:
    # Подавление касается только доставки: складской профиль по-прежнему
    # получает слабый бонус, когда склад упомянут лишь в описании.
    warehouse = _profile(desired_roles=("Lagerarbeiter",), driver_license=None)
    score_result, _, _ = _score(
        "Mitarbeiter (m/w/d) in Vollzeit",
        "Sie unterstützen unser Lager bei der Kommissionierung.",
        profile=warehouse,
    )

    assert "related_role_mention" in {hit.code for hit in score_result.positive_hits}


@pytest.mark.parametrize(
    ("excluded", "title"),
    (
        ("Paketzustellung", "Paketzusteller (m/w/d) in Krefeld"),
        ("Briefzustellung", "Briefzusteller (m/w/d)"),
        ("Postzustellung", "Postbote für Briefe und Pakete (m/w/d)"),
    ),
)
def test_excluded_delivery_activity_also_excludes_its_worker_title(excluded: str, title: str) -> None:
    signals = inspect_vacancy(_canonical(title), _profile(excluded_roles=(excluded,)))

    assert signals.excluded_role_hits, (excluded, title)


def test_excluded_role_morphology_does_not_spread_to_other_words() -> None:
    # Общее отсечение "-ung" превратило бы "Lieferung" в "liefer" и скрыло бы
    # всех Lieferfahrer. Сопоставление обязано остаться узким.
    signals = inspect_vacancy(_canonical("Lieferfahrer (m/w/d)"), _profile(excluded_roles=("Lieferung",)))

    assert not signals.excluded_role_hits
