from __future__ import annotations

from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from app.core.config import Settings
from app.services.driver_license_signal_extractor import extract_driver_license_requirements
from app.services.employment_signal_extractor import extract_employment_signals
from app.services.filter_engine import FilterEngine
from app.services.geo_distance import canonical_city, location_matches_city, resolve_point
from app.services.normalizer import VacancyNormalizer
from app.services.profile_condition_review import condition_review_hits
from app.services.rule_catalog import inspect_vacancy
from app.services.search_models import SearchProfileContext
from app.services.search_service import SearchService, _FetchBudget
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview, SourceSearchInput
from app.services.source_merge import SourceMergeService
from app.services.vehicle_class_signal_extractor import extract_vehicle_class_signals

PROFILE = SearchProfileContext(
    profile_label="Произвольное имя", profile_source="saved", desired_roles=("Перегон автомобилей",),
    driver_license="B", german_level="A1", self_employment_ok=False,
)


def record(body: str = "", *, title: str = "Fahrzeugüberführer", complete: bool | None = None):
    return SourceRecordPreview(
        source_id="test", source_name="Test", external_id="one", source_reference=None,
        title=title, company="Example", location="Berlin", posted_at=None, detail_url=None,
        raw_payload={"description": body}, description_complete=complete,
    )


def group(body: str, *, complete: bool | None = None):
    normalized = VacancyNormalizer().normalize_source_record(record(body, complete=complete))
    return SourceMergeService().merge_records((normalized,)).canonical_groups[0]


@pytest.mark.parametrize("body", [
    "LKW-Erfahrung nicht nötig, nur PKW", "LKW ist nicht erforderlich. Nur PKW bis 3,5 t.",
    "Kein LKW, nur PKW", "Keine Fahrerkarte notwendig", "Code 95 nicht erforderlich",
    "Kein Führerschein C erforderlich", "Führerschein CE ist nicht notwendig",
    "Kein LKW-Führerschein erforderlich, Klasse B genügt", "CE nicht erforderlich",
    "Kein Gewerbeschein notwendig", "Gewerbeschein ist nicht erforderlich",
    "Keine Deutschkenntnisse erforderlich", "Deutsch B2 nicht erforderlich",
    "Keine guten Deutschkenntnisse notwendig", "Gute Deutschkenntnisse sind nicht erforderlich",
    "Deutschkenntnisse A1 erforderlich", "Basic German required",
    "Kein Heben und Tragen erforderlich",
])
def test_explicit_negations_and_basic_german_do_not_reject(body: str) -> None:
    vacancy = group(body)
    result = FilterEngine().evaluate(vacancy, replace(PROFILE, physical_work_ok=False))
    assert not result.hard_reject, result.rejection_hits


@pytest.mark.parametrize(("body", "code"), [
    ("Kein Gewerbeschein notwendig; als Subunternehmer arbeiten", "self_employment_mismatch"),
    ("Keine Festanstellung. Gewerbeschein erforderlich", "self_employment_mismatch"),
    ("Keine Deutschkenntnisse erforderlich. Deutsch B2 zwingend", "strong_german_mismatch"),
    ("Führerschein C nicht erforderlich; Führerschein CE zwingend", "driver_license_mismatch"),
    ("Führerschein C nicht erforderlich; Führerschein C zwingend", "driver_license_mismatch"),
    ("Kein LKW im Nahverkehr. LKW im Fernverkehr", "heavy_vehicle_mismatch"),
    ("LKW fahren; Erfahrung nicht nötig", "heavy_vehicle_mismatch"),
    ("Nicht ohne Gewerbeschein", "self_employment_mismatch"),
])
def test_separate_positive_evidence_survives_negation(body: str, code: str) -> None:
    result = FilterEngine().evaluate(group(body), PROFILE)
    assert code in {hit.code for hit in result.rejection_hits}


def test_negated_license_retains_evidence_without_becoming_a_requirement() -> None:
    result = extract_driver_license_requirements("Kein Führerschein C erforderlich; Führerschein B erforderlich")
    assert result.negated == ("C",)
    assert result.required == ("B",)
    assert result.mentioned == ("B", "C")


def test_negative_context_does_not_cross_a_list_boundary() -> None:
    assert extract_vehicle_class_signals("Keine Erfahrung; LKW fahren").heavy_vehicle
    assert extract_employment_signals("Keine Nachtschicht. Gewerbeschein erforderlich").requires_self_employment


@pytest.mark.parametrize("district", ["Adlershof", "Spandau", "Kreuzberg", "Friedrichshain", "Charlottenburg", "Neukölln"])
def test_berlin_district_dictionary(district: str) -> None:
    assert canonical_city(district) == "berlin"
    assert location_matches_city(district, "Berlin") is True
    assert resolve_point(city=district) == resolve_point(city="Berlin")


@pytest.mark.parametrize("name", ["Biesdorf", "Weissensee", "Rosenthal", "Dahlem"])
def test_ambiguous_district_names_are_not_forced_to_berlin(name: str) -> None:
    assert canonical_city(name) != "berlin"
    assert location_matches_city(name, "Berlin") is None


@pytest.mark.parametrize("complete", [None, False, True])
def test_fragment_provenance_overrides_sentence_appearance(complete: bool) -> None:
    body = "Wir suchen Fahrzeugüberführer. Wir bieten eine strukturierte Einarbeitung und flexible Arbeitszeiten. " * 3
    signals = inspect_vacancy(group(body, complete=complete), PROFILE)
    assert signals.no_mandatory_german_mentioned is (complete is True)
    assert signals.english_requirement_unknown is (complete is not True)
    assert not FilterEngine().evaluate(group(body, complete=complete), PROFILE).hard_reject


def test_explicit_requirement_in_fragment_is_still_used() -> None:
    signals = inspect_vacancy(group("Deutsch B2 zwingend", complete=False), PROFILE)
    assert signals.strong_german_required


def test_lever_requirement_lists_and_closing_are_analyzed_and_raw_is_preserved() -> None:
    from app.services.source_adapters.lever_adapter import _parse_record

    payload = {
        "id": "one", "text": "Fahrzeugüberführer", "description": "Introduction",
        "lists": [{"text": "Anforderungen", "content": "<li>Deutsch B2 zwingend.</li>"}],
        "additionalPlain": "Gewerbeschein erforderlich", "closingPlain": "Führerschein CE erforderlich",
    }
    parsed = _parse_record("lever", "Lever", "test", payload)
    assert parsed is not None and parsed.description_complete is True
    assert parsed.raw_payload["description"] == payload["description"]
    assert parsed.raw_payload["lists"] == payload["lists"]
    normalized = VacancyNormalizer().normalize_source_record(parsed)
    vacancy = SourceMergeService().merge_records((normalized,)).canonical_groups[0]
    signals = inspect_vacancy(vacancy, PROFILE)
    assert signals.strong_german_required and signals.requires_self_employment
    assert "CE" in signals.required_driver_license_categories


def test_car_and_start_preferences_are_used_without_guessing_missing_requirements() -> None:
    profile = replace(PROFILE, car_available=False, start_availability_text="с 16.11.2026")
    hits = condition_review_hits("Eigener PKW erforderlich", profile, immediate_start=True, today=date(2026, 9, 30))
    assert {h.code for h in hits} == {"own_car_review", "start_date_review"}
    assert not condition_review_hits("Kein eigener PKW erforderlich", profile, immediate_start=False)
    assert not condition_review_hits("Keine Angaben", profile, immediate_start=False)
    assert not condition_review_hits("Eigener PKW erforderlich", replace(profile, car_available=None), immediate_start=False)


class Pages(BaseSourceAdapter):
    source_id = "test"
    display_name = "Test"

    def __init__(self, *, title="Fahrzeugüberführer", count=2, repeat=False, error_page=0, total=None):
        self.calls: list[int] = []
        self.title, self.count, self.repeat, self.error_page, self.total = title, count, repeat, error_page, total

    def is_enabled(self):
        return True

    def search(self, search_input):
        self.calls.append(search_input.page)
        if search_input.page == self.error_page:
            raise AdapterRequestError(source_id=self.source_id, source_name=self.display_name,
                                      message="rate limit", status_code=429)
        records = tuple(replace(record(title=self.title), external_id=f"{0 if self.repeat else search_input.page}-{i}")
                        for i in range(self.count))
        return AdapterSearchResponse(self.source_id, self.display_name, records, self.total,
                                     search_input.page, search_input.page_size, {})


@pytest.mark.parametrize(("kwargs", "expected_pages"), [
    ({}, [1, 2, 3, 4]), ({"count": 1}, [1]), ({"repeat": True}, [1, 2]),
    ({"title": "Python Developer"}, [1]), ({"total": 2}, [1]),
])
def test_adaptive_page_stopping(kwargs, expected_pages) -> None:
    adapter = Pages(**kwargs)
    service = SearchService(settings=Settings(search_max_pages=50))
    service._fetch_source_pages(adapter, SourceSearchInput("Fahrzeugüberführer", page_size=2))
    assert adapter.calls == expected_pages


def test_rate_limit_retains_results_and_stops_later_queries() -> None:
    adapter = Pages(error_page=2)
    service = SearchService()
    budget = _FetchBudget()
    query = SourceSearchInput("Fahrzeugüberführer", page_size=2)
    result = service._fetch_source_pages(adapter, query, fetch_budget=budget)
    assert len(result.records) == 2 and result.warnings
    later = service._fetch_source_pages(adapter, replace(query, query="Umsetzfahrer"), fetch_budget=budget)
    assert not later.records and later.warnings
    assert adapter.calls == [1, 2]


def test_shared_budget_and_cancellation_bound_network_calls() -> None:
    adapter = Pages()
    service = SearchService()
    budget = _FetchBudget()
    for _ in range(15):
        service._fetch_source_pages(adapter, SourceSearchInput("Fahrzeugüberführer", page_size=2), fetch_budget=budget)
    assert len(adapter.calls) == 24
    stopped = Event()
    stopped.set()
    service._fetch_source_pages(adapter, SourceSearchInput("Fahrzeugüberführer"), stop_event=stopped)
    assert len(adapter.calls) == 24
