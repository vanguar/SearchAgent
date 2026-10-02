from collections import Counter
from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest
from app.core.config import Settings
from app.services.search_fallback import LIGHT_TRANSPORT_QUERIES, get_light_transport_query_representatives
from app.services.search_service import SearchService, _FetchBudget
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview, SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry
from scripts.diagnose_light_transport import PROFILE


class Adapter(BaseSourceAdapter):
    display_name = "Fixture"

    def __init__(self, source_id, calls):
        self.source_id = source_id
        self.calls = calls

    def is_enabled(self):
        return True

    def search(self, query):
        self.calls.append((self.source_id, query))
        records = tuple(SourceRecordPreview(
            source_id=self.source_id, source_name=self.display_name, external_id=str(i), source_reference=None,
            title="Sprinterfahrer Klasse B", company=f"Company {i}", location=query.location, posted_at=None,
            detail_url=f"https://example.org/{i}", raw_payload={"description": "Transport von Waren."},
        ) for i in range(3))
        return AdapterSearchResponse(self.source_id, self.display_name, records, None, 1, 25, {})


@pytest.mark.parametrize("cities,anchor", [
    ("Neustrelitz, Neubrandenburg, Berlin, Rostock", "Berlin"),
    ("Neustrelitz, Rostock", "Rostock"),
])
@pytest.mark.parametrize("primary", ["Sprinterfahrer", "Fahrer Sonderfahrten"])
def test_full_anchor_diversity_and_two_productive_queries_per_other_city(cities, anchor, primary):
    calls = []
    registry = SourceAdapterRegistry(tuple(Adapter(s, calls) for s in ("ba", "adzuna", "careerjet")))
    service = SearchService(registry=registry, settings=Settings(search_max_pages=4))
    service.get_profile_context = Mock(return_value=PROFILE)
    service._llm_client = Mock()
    result = service.orchestrated_search(
        search_input=SourceSearchInput(primary, location=cities, page_size=25),
        source_ids=("ba", "adzuna", "careerjet"),
    )
    for source in ("ba", "adzuna", "careerjet"):
        legs = [query for sid, query in calls if sid == source]
        assert [(q.query, q.location) for q in legs[:len(LIGHT_TRANSPORT_QUERIES)]] == [
            (q, anchor) for q in get_light_transport_query_representatives(primary)
        ]
        for city in cities.split(", "):
            assert {q.query for q in legs if q.location == city} >= set(LIGHT_TRANSPORT_QUERIES[:2])
    assert len(calls) <= 48
    assert max(Counter(s for s, _ in calls).values()) <= 24
    assert all(q.page == 1 and q.page_size == 25 for _, q in calls)
    assert result.attempt_summary.stop_reason == "sufficient_canonical_results"
    service._llm_client.suggest_fallback_keywords.assert_not_called()


def test_missed_family_and_unrelated_queries_cannot_unlock_early_stop():
    budget = _FetchBudget(vehicle_diversity=True, diversity_queries=LIGHT_TRANSPORT_QUERIES)
    for query in (*LIGHT_TRANSPORT_QUERIES[:-1], "Fahrer", "Logistik"):
        budget.record_response("ba", query, empty=False)
    assert not budget.diversity_ready()
    budget.record_response("ba", LIGHT_TRANSPORT_QUERIES[-1], empty=True)
    assert budget.diversity_ready()


def test_spelling_variants_and_unknown_terms_cannot_expand_the_plan():
    assert len(get_light_transport_query_representatives("Sprinter-Fahrer")) == 6
    assert get_light_transport_query_representatives("Fahrer") == LIGHT_TRANSPORT_QUERIES
    assert not {"Fahrer", "Logistik", "Nahverkehr", "Transport"} & set(LIGHT_TRANSPORT_QUERIES)


def test_cancelled_search_makes_no_calls():
    calls = []
    service = SearchService(registry=SourceAdapterRegistry((Adapter("ba", calls),)))
    service.get_profile_context = Mock(return_value=replace(PROFILE, search_query_terms=()))
    stop = Event()
    stop.set()
    service.orchestrated_search(search_input=SourceSearchInput("Sprinterfahrer", location="Berlin"), source_ids=("ba",), stop_event=stop)
    assert not calls
