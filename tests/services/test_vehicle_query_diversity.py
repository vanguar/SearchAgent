from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest
from app.core.config import Settings
from app.services.search_fallback import get_vehicle_query_representatives, vehicle_query_family
from app.services.search_models import SearchProfileContext
from app.services.search_service import SearchService, _FetchBudget
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError, HttpTransportError
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview, SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry

PROFILE = SearchProfileContext(
    profile_label="Перегон автомобилей", profile_source="saved", driver_license="B",
    desired_roles=("Fahrzeugüberführer",), german_level="A1", self_employment_ok=False,
    search_query_terms=("Fahrzeugüberführer", "Fahrzeugüberführung", "Überführungsfahrer"),
    additional_search_terms=("Fahrzeugtransfer", "Hol- und Bringfahrer", "PKW-Überführung",
                             "Mietwagenüberführung", "Fahrzeugzustellung"),
)
INPUT = SourceSearchInput("Fahrzeugüberführer", location="Neustrelitz, Neubrandenburg, Berlin, Rostock",
                          radius_km=100, page_size=3)
SOURCES = ("ba", "careerjet", "adzuna", "jooble", "arbeitnow")


class Adapter(BaseSourceAdapter):
    display_name = "Test"

    def __init__(self, source_id, calls, *, records=False, forbidden=False):
        self.source_id, self.calls = source_id, calls
        self.records, self.forbidden = records, forbidden

    def is_enabled(self):
        return True

    def search(self, query):
        self.calls.append((self.source_id, query))
        if self.forbidden:
            raise AdapterRequestError(source_id=self.source_id, source_name=self.display_name,
                                      message="HTTP 403", status_code=403)
        records = tuple(SourceRecordPreview(
            source_id=self.source_id, source_name=self.display_name, external_id=str(i), source_reference=None,
            title=f"Fahrzeugüberführer {i}", company=f"Company {i}", location="Berlin", posted_at=None,
            detail_url=f"https://example.org/{i}", raw_payload={"description": "Führerschein B. Deutsch A1."},
        ) for i in range(3)) if self.records else ()
        return AdapterSearchResponse(self.source_id, self.display_name, records, None,
                                     query.page, query.page_size, {})


def service(*, records=False):
    calls = []
    adapters = tuple(Adapter(s, calls, records=records and s == "ba", forbidden=s == "jooble") for s in SOURCES)
    svc = SearchService(registry=SourceAdapterRegistry(adapters), settings=Settings(search_max_pages=4))
    svc.get_profile_context = Mock(return_value=PROFILE)
    return svc, calls


def test_profile_grammar_variants_do_not_crowd_out_query_families():
    plan = get_vehicle_query_representatives(INPUT.query, (*PROFILE.search_query_terms, *PROFILE.additional_search_terms))
    assert len(plan) == 8
    assert len({vehicle_query_family(q) for q in plan}) == 8
    assert "Fahrzeugüberführung" not in plan
    assert "Fahrzeugzustellung" not in plan  # represented by Fahrzeugtransfer
    assert {"Hol- und Bringfahrer", "Transferfahrer", "Mietwagenüberführer", "Fuhrparkfahrer"} <= set(plan)
    assert plan[0] == INPUT.query


def test_sufficient_results_wait_for_all_families_in_largest_city():
    svc, calls = service(records=True)
    result = svc.orchestrated_search(search_input=INPUT, source_ids=SOURCES)
    queries = list(dict.fromkeys(q.query for source, q in calls if source == "ba"))
    assert len(result.maybe_results) + len(result.hot_results) >= 3
    assert len({vehicle_query_family(q) for q in queries}) == 8
    # Berlin is the largest requested city, although the form lists it third.
    ba_legs = [(q.query, q.location) for sid, q in calls if sid == "ba"]
    assert {city for _, city in ba_legs[:8]} == {"Berlin"}
    for source in ("ba", "adzuna", "careerjet"):
        assert len({q.query for sid, q in calls if sid == source and q.location == "Berlin"}) == 8
    # Early stop still waits for one primary-query leg in every other city.
    assert ba_legs[8:] == [(INPUT.query, city) for city in ("Neustrelitz", "Neubrandenburg", "Rostock")]
    assert all(q.radius_km == 100 and q.page == 1 for _, q in calls)
    assert [source for source, _ in calls[:5]] == ["ba", "adzuna", "careerjet", "jooble", "arbeitnow"]
    assert sum(source == "jooble" for source, _ in calls) == 1
    assert sum(source == "arbeitnow" for source, _ in calls) == 2
    assert result.attempt_summary.stop_reason == "sufficient_canonical_results"
    assert len(calls) <= 48


def test_empty_results_fill_remaining_combinations_within_unchanged_budget():
    svc, calls = service()
    result = svc.orchestrated_search(search_input=INPUT, source_ids=SOURCES)
    assert len(calls) == 48
    assert all(sum(source == sid for source, _ in calls) <= 24 for sid in SOURCES)
    assert len({vehicle_query_family(q.query) for _, q in calls}) == 8
    ba_pairs = [(q.query, q.location) for source, q in calls if source == "ba"]
    assert {location for _, location in ba_pairs[:8]} == {"Berlin"}
    assert len({query for query, _ in ba_pairs[:8]}) == 8
    assert len(ba_pairs) == len(set(ba_pairs))
    assert len(ba_pairs) > 8  # second pass visits other cities for each representative
    assert all(q.page == 1 for _, q in calls)
    assert result.attempt_summary.stop_reason == "source_budget_or_blocked"


def test_failed_or_unexecuted_families_do_not_unlock_early_stop():
    budget = _FetchBudget(vehicle_diversity=True)
    svc = SearchService()
    calls = []
    adapter = Adapter("jooble", calls, forbidden=True)
    with pytest.raises(AdapterRequestError):
        svc._fetch_source_pages(adapter, INPUT, fetch_budget=budget)
    for query in ("Fahrzeugtransfer", "Hol- und Bringfahrer", "Mietwagenüberführer", "Fleet Driver"):
        svc._fetch_source_pages(adapter, replace(INPUT, query=query), fetch_budget=budget)
    assert len(calls) == 1
    assert not budget.diversity_ready()
    for query in ("Fahrzeugüberführer", "Fahrzeugüberführung", "Fahrzeugüberführerin"):
        budget.record_response("ba", query, empty=False)
    assert not budget.diversity_ready()


def test_wrapped_arbeitnow_limit_blocks_only_this_vehicle_run():
    svc = SearchService()
    adapter = Adapter("arbeitnow", [])
    def limited(query):
        cause = HttpTransportError(url="https://example.org", message="limited", status_code=429)
        raise AdapterRequestError(source_id="arbeitnow", source_name="Test", message="limited") from cause
    adapter.search = limited
    budget = _FetchBudget(vehicle_diversity=True)
    with pytest.raises(AdapterRequestError):
        svc._fetch_source_pages(adapter, INPUT, fetch_budget=budget)
    assert not budget.has_capacity(("arbeitnow",))
    assert _FetchBudget(vehicle_diversity=True).has_capacity(("arbeitnow",))


def test_cancelled_vehicle_run_does_not_start_sources():
    svc, calls = service()
    stopped = Event()
    stopped.set()
    result = svc.orchestrated_search(search_input=INPUT, source_ids=SOURCES, stop_event=stopped)
    assert not calls
    assert result.attempt_summary.stop_reason == "cancelled"


def test_single_city_exhausts_compact_plan_without_llm_or_extra_pages():
    svc, calls = service()
    svc._llm_client = Mock()
    result = svc.orchestrated_search(search_input=replace(INPUT, location="Berlin"), source_ids=SOURCES)
    assert len({q.query for _, q in calls}) == 8
    assert all(q.location == "Berlin" and q.page == 1 for _, q in calls)
    assert result.attempt_summary.stop_reason == "queries_exhausted"
    svc._llm_client.suggest_fallback_keywords.assert_not_called()


@pytest.mark.parametrize("primary", ["Fahrzeugüberführung", "Mietwagenüberführer", "Hol- und Bringfahrer"])
def test_alternative_submitted_query_keeps_priority_without_duplicate_family(primary):
    plan = get_vehicle_query_representatives(primary, PROFILE.search_query_terms)
    assert plan[0] == primary
    assert len(plan) == len({vehicle_query_family(q) for q in plan}) == 8
