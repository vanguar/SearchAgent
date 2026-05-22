from __future__ import annotations

from app.core.config import Settings
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.lever_adapter import LeverAdapter
from app.services.source_adapters.models import SourceSearchInput


class FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get_json(self, url: str, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def test_lever_adapter_maps_company_postings() -> None:
    payload = [
        {
            "id": "abc",
            "text": "Delivery Platform Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/abc",
            "createdAt": "1770000000000",
            "categories": {"location": "Berlin, Germany", "team": "Engineering"},
            "descriptionPlain": "Build delivery tooling.",
        }
    ]
    transport = FixtureTransport(payload)
    adapter = LeverAdapter(
        settings=Settings(source_lever_enabled=True, source_lever_company_slugs="acme"),
        http_transport=transport,
    )

    response = adapter.search(SourceSearchInput(query="delivery", search_mode="germany_local", page=1, page_size=10))

    assert transport.calls[0]["url"].endswith("/acme")
    assert transport.calls[0]["params"] == {"mode": "json"}
    assert response.total_count == 1
    assert response.records[0].external_id == "acme:abc"
    assert response.records[0].location == "Berlin, Germany"
    assert response.records[0].raw_payload["description"] == "Build delivery tooling."


def test_lever_descriptor_disabled_without_company_slugs() -> None:
    descriptor = LeverAdapter(settings=Settings(source_lever_enabled=True, source_lever_company_slugs="")).describe()

    assert descriptor.enabled is False
    assert descriptor.status_kind == "disabled"
