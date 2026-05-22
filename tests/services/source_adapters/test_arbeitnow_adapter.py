from __future__ import annotations

from app.core.config import Settings
from app.services.source_adapters.arbeitnow_adapter import ArbeitnowAdapter
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput


class FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get_json(self, url: str, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def _adapter(payload: object) -> ArbeitnowAdapter:
    return ArbeitnowAdapter(settings=Settings(source_arbeitnow_enabled=True), http_transport=FixtureTransport(payload))


def test_arbeitnow_adapter_filters_germany_jobs_by_query() -> None:
    payload = {
        "data": [
            {
                "slug": "driver-1",
                "title": "Fahrer",
                "company_name": "Fleet",
                "location": "Berlin, Germany",
                "remote": False,
                "url": "https://www.arbeitnow.com/jobs/driver-1",
                "tags": ["delivery"],
                "created_at": "2026-05-20",
            },
            {
                "slug": "sales-1",
                "title": "Sales",
                "location": "Paris, France",
                "url": "https://www.arbeitnow.com/jobs/sales-1",
            },
        ]
    }

    adapter = _adapter(payload)
    response = adapter.search(SourceSearchInput(query="fahrer", search_mode="germany_local", page=2, page_size=10))

    assert adapter.http_transport.calls[0]["params"] == {"page": 2}  # type: ignore[attr-defined]
    assert response.total_count == 1
    assert response.records[0].external_id == "driver-1"
    assert response.records[0].location == "Berlin, Germany"


def test_arbeitnow_adapter_filters_remote_worldwide_jobs() -> None:
    payload = {
        "data": [
            {"slug": "py-remote", "title": "Python Engineer", "remote": True, "location": "Remote", "url": "https://example/1"},
            {"slug": "py-local", "title": "Python Engineer", "remote": False, "location": "Berlin, Germany", "url": "https://example/2"},
        ]
    }

    response = _adapter(payload).search(
        SourceSearchInput(query="python", search_mode="remote_worldwide", location="remote", page=1, page_size=10)
    )

    assert [record.external_id for record in response.records] == ["py-remote"]
