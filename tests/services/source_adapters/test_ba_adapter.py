import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.services.search_models import SearchProfileContext
from app.services.search_service import SearchService
from app.services.source_adapters.ba_adapter import BAAdapter
from app.services.source_adapters.errors import (
    AdapterRequestError,
    AdapterResponseError,
    HttpDecodeError,
    HttpTransportError,
)
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry

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


class FailingTransport:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_json(self, url: str, **kwargs: object) -> HttpJsonResponse:
        _ = kwargs
        raise self.error


def _load_fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_ba_adapter_maps_response_into_common_preview_contract() -> None:
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = BAAdapter(settings=Settings(), http_transport=transport)

    response = adapter.search(
        SourceSearchInput(query="lager", location="Berlin", radius_km=25, page=1, page_size=5)
    )

    assert transport.calls[0]["url"] == "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
    assert transport.calls[0]["headers"] == {
        "X-API-Key": "jobboerse-jobsuche",
        "Accept": "application/json",
    }
    assert transport.calls[0]["params"] == {
        "was": "lager",
        "page": 1,
        "size": 5,
        "wo": "Berlin",
        "umkreis": 25,
    }

    assert response.source_id == "ba"
    assert response.total_count == 42
    assert len(response.records) == 2
    assert response.raw_payload == payload
    assert len(transport.calls) == 1

    first_record = response.records[0]
    assert first_record.external_id == "10000-1234567890-S"
    assert first_record.source_reference == "10000-1234567890-S"
    assert first_record.title == "Lagermitarbeiter/in"
    assert first_record.company == "Logistik Nord GmbH"
    assert first_record.location == "10115 Berlin, Deutschland"
    assert first_record.posted_at == "2026-04-15"
    assert first_record.detail_url == "https://example.org/jobs/10000-1234567890-S"
    assert first_record.raw_payload["referenznummer"] == "10000-1234567890-S"

    second_record = response.records[1]
    assert second_record.external_id == "10000-9876543210-S"
    assert second_record.posted_at == "2026-04-14"
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
    assert "wo" not in params
    assert "umkreis" not in params


def test_ba_adapter_maps_search_input_to_v6_parameters() -> None:
    transport = FixtureTransport({"ergebnisliste": [], "maxErgebnisse": 0})
    adapter = BAAdapter(settings=Settings(), http_transport=transport)

    adapter.search(
        SourceSearchInput(
            query="Fahrer Klasse B",
            location="Berlin",
            radius_km=25,
            page=1,
            page_size=8,
        )
    )

    assert transport.calls[0]["params"] == {
        "was": "Fahrer Klasse B",
        "page": 1,
        "size": 8,
        "wo": "Berlin",
        "umkreis": 25,
    }


def test_ba_adapter_omits_location_and_radius_for_nationwide_germany() -> None:
    transport = FixtureTransport({"ergebnisliste": [], "maxErgebnisse": 0})
    adapter = BAAdapter(settings=Settings(), http_transport=transport)

    adapter.search(
        SourceSearchInput(
            query="Fahrer Klasse B",
            location="Deutschland",
            radius_km=50,
            page=1,
            page_size=5,
        )
    )

    params = transport.calls[0]["params"]
    assert params == {
        "was": "Fahrer Klasse B",
        "page": 1,
        "size": 5,
    }


def test_ba_adapter_returns_success_for_empty_result_list() -> None:
    adapter = BAAdapter(
        settings=Settings(),
        http_transport=FixtureTransport({"ergebnisliste": [], "maxErgebnisse": 0}),
    )

    response = adapter.search(SourceSearchInput(query="Fahrer Klasse B"))

    assert response.records == ()
    assert response.total_count == 0
    assert response.warnings == ()


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
    adapter = BAAdapter(settings=Settings(), http_transport=FixtureTransport({"ergebnisliste": "broken"}))

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert exc_info.value.code == "response_invalid"
    assert "ergebnisliste" in exc_info.value.message


def test_ba_adapter_skips_malformed_records_without_losing_valid_jobs() -> None:
    fixture_records = _load_fixture()["ergebnisliste"]
    assert isinstance(fixture_records, list)
    valid_record = fixture_records[0]
    payload = {
        "ergebnisliste": [42, {"stellenangebotsTitel": "Ohne ID"}, valid_record],
        "maxErgebnisse": 3,
    }
    adapter = BAAdapter(settings=Settings(), http_transport=FixtureTransport(payload))

    response = adapter.search(SourceSearchInput(query="lager"))

    assert len(response.records) == 1
    assert response.records[0].external_id == "10000-1234567890-S"
    assert response.warnings
    assert "2" in response.warnings[0]


def test_ba_adapter_preserves_403_as_source_request_error() -> None:
    adapter = BAAdapter(
        settings=Settings(),
        http_transport=FailingTransport(
            HttpTransportError(
                url="https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
                message="No match found for request",
                status_code=403,
            )
        ),
    )

    with pytest.raises(AdapterRequestError) as exc_info:
        adapter.search(SourceSearchInput(query="Fahrer Klasse B"))

    error = exc_info.value
    assert error.status_code == 403
    assert error.retryable is False
    assert "HTTP 403" in error.message
    assert "No match found for request" not in error.message


def test_ba_403_becomes_search_service_source_error_not_empty_success() -> None:
    adapter = BAAdapter(
        settings=Settings(),
        http_transport=FailingTransport(
            HttpTransportError(
                url="https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
                message="No match found for request",
                status_code=403,
            )
        ),
    )
    service = SearchService(registry=SourceAdapterRegistry(adapters=(adapter,)))

    result = service.search(
        search_input=SourceSearchInput(query="Fahrer Klasse B"),
        source_ids=("ba",),
        profile=SearchProfileContext(
            profile_label="Driver B",
            profile_source="saved",
            desired_roles=("Driver B – Fernverkehr",),
        ),
        enrich_with_llm=False,
    )

    assert result.total_raw_records == 0
    assert len(result.source_states) == 1
    source_state = result.source_states[0]
    assert source_state.status_kind == "error"
    assert source_state.status_label == "Ошибка"
    assert source_state.error_message is not None
    assert "HTTP 403" in source_state.error_message


@pytest.mark.parametrize(
    ("status_code", "expected_text", "retryable"),
    (
        (404, "HTTP 404", False),
        (429, "HTTP 429", True),
        (503, "HTTP 503", True),
        (None, "время ожидания", True),
    ),
)
def test_ba_adapter_distinguishes_upstream_request_failures(
    status_code: int | None,
    expected_text: str,
    retryable: bool,
) -> None:
    message = "Request timed out." if status_code is None else "upstream error"
    adapter = BAAdapter(
        settings=Settings(),
        http_transport=FailingTransport(
            HttpTransportError(url="https://example.org/ba", message=message, status_code=status_code)
        ),
    )

    with pytest.raises(AdapterRequestError) as exc_info:
        adapter.search(SourceSearchInput(query="Fahrer Klasse B"))

    assert expected_text in exc_info.value.message
    assert exc_info.value.retryable is retryable


def test_ba_adapter_reports_malformed_json_as_response_error() -> None:
    adapter = BAAdapter(
        settings=Settings(),
        http_transport=FailingTransport(
            HttpDecodeError(url="https://example.org/ba", message="Response body is not valid JSON.")
        ),
    )

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="Fahrer Klasse B"))

    assert "некорректный JSON" in exc_info.value.message
