from collections import Counter
from dataclasses import replace
from unittest.mock import Mock

from app.core.config import Settings
from app.services.search_models import SearchProfileContext
from app.services.search_service import SearchService
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview, SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.services.warehouse_transport import WAREHOUSE_ROUTE_ROLE

CITIES = ("Berlin", "Neustrelitz", "Neubrandenburg", "Rostock")
QUERIES = ("Fahrer Klasse B", "Fahrer Direktfahrten", "Lagerhelfer", "Reinigungskraft")
PROFILE = SearchProfileContext(
    profile_label="Mixed strict", profile_source="saved", strict_match_mode="a1", driver_license="B",
    german_level="A1", english_level="none",
    desired_roles=(WAREHOUSE_ROUTE_ROLE, "Lagerhelfer", "Produktionshelfer", "Reinigungskraft"),
    search_query_terms=QUERIES, additional_search_terms=("Produktionshelfer", "Sprinterfahrer"),
    preferred_locations=CITIES,
)


class FullPageAdapter(BaseSourceAdapter):
    def __init__(self, source_id):
        self.source_id = source_id
        self.display_name = source_id
        self.calls = []

    def is_enabled(self):
        return True

    def search(self, search_input):
        self.calls.append(search_input)
        # A full page that would allow pagination. No matching work, so all
        # explicit profile queries must still be attempted within 48 calls.
        record = SourceRecordPreview(
            source_id=self.source_id, source_name=self.display_name,
            external_id=f"{search_input.query}-{search_input.location}-{search_input.page}",
            source_reference=None, title="Vacancy", company="Example", location=search_input.location,
            posted_at=None, detail_url="https://example.org/job", raw_payload={},
        )
        return AdapterSearchResponse(self.source_id, self.display_name, (record,), 100, search_input.page, 1, {})


def test_strict_mixed_search_reaches_driver_and_other_roles_in_all_four_cities():
    adapters = [FullPageAdapter(source) for source in ("ba", "careerjet", "adzuna")]
    service = SearchService(registry=SourceAdapterRegistry(adapters=adapters), settings=Settings(search_max_pages=4))
    service.get_profile_context = Mock(return_value=PROFILE)
    service.orchestrated_search(
        search_input=SourceSearchInput(query=QUERIES[0], location=", ".join(CITIES), radius_km=0, page_size=1),
        source_ids=("ba", "careerjet", "adzuna"), profile_id=11,
    )
    for adapter in adapters:
        assert {call.page for call in adapter.calls} == {1}
        assert {call.query for call in adapter.calls} >= set(QUERIES)
        for query in QUERIES:
            assert {c.location for c in adapter.calls if c.query == query} == set(CITIES)
    assert sum(len(adapter.calls) for adapter in adapters) <= 48


def test_ordinary_profile_retains_configured_pagination():
    adapter = FullPageAdapter("ba")
    service = SearchService(registry=SourceAdapterRegistry(adapters=(adapter,)), settings=Settings(search_max_pages=4))
    service.search(search_input=SourceSearchInput(query="Helfer", location="Berlin", page_size=1),
                   profile=replace(PROFILE, strict_match_mode="off"), enrich_with_llm=False)
    assert Counter(c.page for c in adapter.calls) == {1: 1, 2: 1, 3: 1, 4: 1}
