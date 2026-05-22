from __future__ import annotations

from app.core.config import Settings
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput
from app.services.source_adapters.remotejobs_adapter import RemoteJobsOrgAdapter


class FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get_json(self, url: str, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def test_remotejobs_adapter_maps_response_and_sets_remote_flag() -> None:
    payload = {
        "data": [
            {
                "id": 101,
                "title": "Python Backend Developer",
                "company": {"name": "Acme"},
                "location": "Remote",
                "posted_at": "2026-05-21",
                "url": "https://remotejobs.org/jobs/101",
                "apply_url": "https://remotejobs.org/apply/101",
                "salary_text": "EUR 70k",
            }
        ],
        "pagination": {"total": 1},
    }
    transport = FixtureTransport(payload)
    adapter = RemoteJobsOrgAdapter(
        settings=Settings(source_remotejobs_enabled=True, source_remotejobs_base_url="https://remotejobs.org/api/v1/jobs"),
        http_transport=transport,
    )

    response = adapter.search(SourceSearchInput(query="python", search_mode="remote_worldwide", page=2, page_size=25))

    assert transport.calls[0]["params"] == {"limit": 25, "offset": 25, "q": "python"}
    assert response.total_count == 1
    assert response.records[0].external_id == "101"
    assert response.records[0].company == "Acme"
    assert response.records[0].raw_payload["salary"] == "EUR 70k"


def test_remotejobs_descriptor_is_global_remote() -> None:
    descriptor = RemoteJobsOrgAdapter(settings=Settings(source_remotejobs_enabled=True)).describe()

    assert descriptor.global_remote is True
