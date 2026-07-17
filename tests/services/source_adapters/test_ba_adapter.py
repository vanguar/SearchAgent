import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.services.source_adapters.ba_adapter import BAAdapter
from app.services.source_adapters.errors import AdapterResponseError
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "ba_search_response.json"


class FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        self.calls.append(
            {
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "timeout_seconds": timeout_seconds,
            }
        )
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)


def _load_fixture() -> object:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_ba_adapter_maps_response_into_common_preview_contract() -> None:
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = BAAdapter(settings=Settings(), http_transport=transport)

    response = adapter.search(
        SourceSearchInput(query="lager", location="Berlin", radius_km=25, page=1, page_size=5)
    )

    assert transport.calls[0]["url"] == "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
    assert transport.calls[0]["headers"] == {"X-API-Key": "jobboerse-jobsuche"}
    assert transport.calls[0]["params"] == {
        "was": "lager",
        "wo": "Berlin",
        "umkreis": 25,
        "page": 1,
        "size": 5,
        "angebotsart": 1,
    }

    assert response.source_id == "ba"
    assert response.total_count == 42
    assert len(response.records) == 2
    assert response.raw_payload == payload

    first_record = response.records[0]
    assert first_record.external_id == "10000-1234567890-S"
    assert first_record.source_reference == "10000-1234567890-S"
    assert first_record.title == "Lagermitarbeiter/in"
    assert first_record.company == "Logistik Nord GmbH"
    assert first_record.location == "10115 Berlin, Deutschland"
    assert first_record.posted_at == "2026-04-15"
    assert first_record.detail_url == "https://example.org/jobs/10000-1234567890-S"
    assert first_record.raw_payload["hashId"] == "hash-1"

    second_record = response.records[1]
    assert second_record.external_id == "10000-9876543210-S"
    assert second_record.detail_url == (
        "https://www.arbeitsagentur.de/jobsuche/suche"
        "?angebotsart=1&id=10000-9876543210-S&was=10000-9876543210-S"
    )


def test_ba_adapter_omits_remote_as_location_for_remote_worldwide_mode() -> None:
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = BAAdapter(settings=Settings(), http_transport=transport)

    adapter.search(
        SourceSearchInput(
            query="python developer",
            location="remote",
            search_mode="remote_worldwide",
            page=1,
            page_size=5,
        )
    )

    params = transport.calls[0]["params"]
    assert params["was"] == "python developer"
    assert params["wo"] is None


def test_ba_adapter_builds_fallback_detail_url_from_hash_id_when_refnr_is_missing() -> None:
    payload = {
        "stellenangebote": [
            {
                "hashId": "14284-9b8898cb050e408-S",
                "beruf": "Staplerfahrer (m/w/d)",
                "arbeitgeber": "Noerpel Logistics & Services GmbH",
                "arbeitsort": {
                    "plz": 88339,
                    "ort": "Bad Waldsee",
                    "region": "Baden-Wurttemberg",
                    "land": "Deutschland",
                },
            }
        ]
    }
    adapter = BAAdapter(settings=Settings(), http_transport=FixtureTransport(payload))

    response = adapter.search(SourceSearchInput(query="stapler"))

    assert response.records[0].external_id == "14284-9b8898cb050e408-S"
    assert response.records[0].detail_url == (
        "https://www.arbeitsagentur.de/jobsuche/suche"
        "?angebotsart=1&id=14284-9b8898cb050e408-S&was=14284-9b8898cb050e408-S"
    )


def test_ba_adapter_raises_typed_error_for_invalid_search_payload() -> None:
    adapter = BAAdapter(settings=Settings(), http_transport=FixtureTransport({"stellenangebote": "broken"}))

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert exc_info.value.code == "response_invalid"
    assert "stellenangebote" in exc_info.value.message
