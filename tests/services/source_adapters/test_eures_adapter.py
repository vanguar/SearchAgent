import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.source_adapters.errors import (
    AdapterConfigurationError,
    AdapterDisabledError,
    AdapterResponseError,
)
from app.services.source_adapters.eures_adapter import EURESAdapter
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "eures_search_response.json"

_ENABLED_SETTINGS = Settings(
    source_eures_enabled=True,
    source_eures_api_key="test-eures-key-abc",
    source_eures_base_url="https://jobsearch.api.eures.europa.eu/datamodel/v2/JobSearch",
)


class PostFixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def get_json(self, url, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        raise AssertionError("EURES не должен использовать get_json")

    def post_json(self, url, *, body=None, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append(
            {"url": url, "body": body or {}, "headers": headers or {}}
        )
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def _load_fixture() -> object:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_eures_adapter_maps_response_into_common_preview_contract() -> None:
    payload = _load_fixture()
    transport = PostFixtureTransport(payload)
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(
        SourceSearchInput(query="lager", page=1, page_size=10)
    )

    assert transport.calls[0]["url"] == "https://jobsearch.api.eures.europa.eu/datamodel/v2/JobSearch"
    # EURES всегда ищет только по Германии
    assert transport.calls[0]["body"]["countries"] == ["DE"]
    assert transport.calls[0]["body"]["keywords"] == "lager"
    # Страница 1 → page_zero_based=0
    assert transport.calls[0]["body"]["page"] == 0
    assert transport.calls[0]["headers"]["User-Api-Key"] == "test-eures-key-abc"

    assert response.source_id == "eures"
    assert response.total_count == 34
    # Третья запись (без handle) должна быть пропущена
    assert len(response.records) == 2
    assert len(response.warnings) == 1
    assert "пропущено 1" in response.warnings[0]

    first = response.records[0]
    assert first.external_id == "JV-DE-20260001"
    assert first.source_reference == "JV-DE-20260001"
    assert first.title == "Lagerhelfer (m/w/d)"
    assert first.company == "Spedition Müller GmbH"
    assert first.location == "Berlin, DE"
    assert first.posted_at == "2026-04-15"
    assert first.detail_url == "https://eures.europa.eu/jobs/JV-DE-20260001"
    assert first.raw_payload["header"]["handle"] == "JV-DE-20260001"

    second = response.records[1]
    assert second.external_id == "JV-DE-20260002"
    assert second.location == "Düsseldorf, DE"


def test_eures_adapter_paginates_with_zero_based_page() -> None:
    transport = PostFixtureTransport({"data": {"totalMatchingCount": 0, "payload": []}})
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="helfer", page=3, page_size=10))

    assert transport.calls[0]["body"]["page"] == 2


def test_eures_adapter_germany_local_searches_germany() -> None:
    transport = PostFixtureTransport({"data": {"totalMatchingCount": 0, "payload": []}})
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="lager", location="France"))

    assert transport.calls[0]["body"]["countries"] == ["DE"]


def test_eures_adapter_remote_worldwide_does_not_force_germany() -> None:
    transport = PostFixtureTransport({"data": {"totalMatchingCount": 0, "payload": []}})
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="python", location="remote", search_mode="remote_worldwide"))

    assert "countries" not in transport.calls[0]["body"]


def test_eures_adapter_raises_when_api_key_not_configured() -> None:
    settings = Settings(source_eures_enabled=True, source_eures_api_key=None)
    adapter = EURESAdapter(settings=settings, http_transport=PostFixtureTransport({}))

    with pytest.raises(AdapterConfigurationError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert "SOURCE_EURES_API_KEY" in exc_info.value.message


def test_eures_adapter_raises_when_disabled() -> None:
    settings = Settings(source_eures_enabled=False, source_eures_api_key="key")
    adapter = EURESAdapter(settings=settings, http_transport=PostFixtureTransport({}))

    with pytest.raises(AdapterDisabledError):
        adapter.search(SourceSearchInput(query="lager"))


def test_eures_adapter_raises_for_missing_data_field() -> None:
    transport = PostFixtureTransport({"error": "bad request"})
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert "'data'" in exc_info.value.message


def test_eures_adapter_raises_for_non_list_payload() -> None:
    transport = PostFixtureTransport({"data": {"payload": "broken", "totalMatchingCount": 0}})
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert "payload" in exc_info.value.message


def test_eures_adapter_raw_payload_is_preserved() -> None:
    payload = _load_fixture()
    transport = PostFixtureTransport(payload)
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="lager"))

    assert response.raw_payload == payload


def test_eures_adapter_builds_fallback_detail_url_from_handle() -> None:
    payload = {
        "data": {
            "totalMatchingCount": 1,
            "payload": [
                {
                    "header": {"handle": "JV-DE-99999"},
                    "jobVacancy": {"jobTitle": "Helfer"},
                }
            ],
        }
    }
    transport = PostFixtureTransport(payload)
    adapter = EURESAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="helfer"))

    assert response.records[0].detail_url == "https://eures.europa.eu/jobs/JV-DE-99999"
