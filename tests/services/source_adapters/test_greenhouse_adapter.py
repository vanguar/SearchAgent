from __future__ import annotations

from app.core.config import Settings
from app.services.source_adapters.greenhouse_adapter import GreenhouseAdapter
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput


class FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get_json(self, url: str, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def test_greenhouse_adapter_maps_company_board_jobs() -> None:
    payload = {
        "jobs": [
            {
                "id": 11,
                "title": "Python Backend Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
                "updated_at": "2026-05-21T10:00:00Z",
                "location": {"name": "Remote - Europe"},
                "content": "<p>Python APIs</p>",
            }
        ]
    }
    transport = FixtureTransport(payload)
    adapter = GreenhouseAdapter(
        settings=Settings(source_greenhouse_enabled=True, source_greenhouse_board_tokens="acme"),
        http_transport=transport,
    )

    response = adapter.search(
        SourceSearchInput(query="python", search_mode="remote_worldwide", location="remote", page=1, page_size=10)
    )

    assert transport.calls[0]["url"].endswith("/acme/jobs")
    assert transport.calls[0]["params"] == {"content": "true"}
    assert response.total_count == 1
    assert response.records[0].external_id == "acme:11"
    assert response.records[0].company == "acme"
    assert response.records[0].raw_payload["description"].strip() == "Python APIs"


def test_greenhouse_descriptor_disabled_without_board_tokens() -> None:
    descriptor = GreenhouseAdapter(settings=Settings(source_greenhouse_enabled=True, source_greenhouse_board_tokens="")).describe()

    assert descriptor.enabled is False
    assert descriptor.status_kind == "disabled"
