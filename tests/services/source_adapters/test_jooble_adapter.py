from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.source_adapters.errors import AdapterConfigurationError
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.jooble_adapter import JoobleAdapter
from app.services.source_adapters.models import SourceSearchInput


class FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get_json(self, url: str, **kwargs: object) -> HttpJsonResponse:
        raise AssertionError("Jooble должен использовать POST")

    def post_json(self, url: str, *, body=None, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "body": body or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def _settings() -> Settings:
    return Settings(source_jooble_enabled=True, jooble_api_key="test-key")


def test_jooble_adapter_posts_search_body_and_maps_jobs() -> None:
    payload = {
        "totalCount": 2,
        "jobs": [
            {
                "id": "j1",
                "title": "Fahrer",
                "company": "Logistik GmbH",
                "location": "Rostock",
                "updated": "2026-05-20",
                "link": "https://jooble.org/job/j1",
                "salary": "2500 EUR",
            }
        ],
    }
    transport = FixtureTransport(payload)
    adapter = JoobleAdapter(settings=_settings(), http_transport=transport)  # type: ignore[arg-type]

    response = adapter.search(SourceSearchInput(query="fahrer", location="Rostock", radius_km=50, page=2, page_size=10))

    assert transport.calls[0]["url"].endswith("/test-key")
    body = transport.calls[0]["body"]
    assert body["keywords"] == "fahrer"
    assert body["location"] == "Rostock"
    assert body["page"] == "2"
    assert body["ResultOnPage"] == 10
    assert body["radius"] == "40"
    assert response.total_count == 2
    assert response.records[0].external_id == "j1"
    assert response.records[0].raw_payload["salary"] == "2500 EUR"


def test_jooble_adapter_requires_api_key() -> None:
    adapter = JoobleAdapter(
        settings=Settings(source_jooble_enabled=True, jooble_api_key=None),
        http_transport=FixtureTransport({}),
    )

    with pytest.raises(AdapterConfigurationError):
        adapter.search(SourceSearchInput(query="fahrer"))


def test_jooble_descriptor_is_error_without_api_key() -> None:
    adapter = JoobleAdapter(settings=Settings(source_jooble_enabled=True, jooble_api_key=None))

    descriptor = adapter.describe()

    assert descriptor.source_id == "jooble"
    assert descriptor.status_kind == "error"
    assert descriptor.enabled is True
