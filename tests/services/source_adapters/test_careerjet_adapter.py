import base64
import hashlib
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.source_adapters.careerjet_adapter import CareerjetAdapter
from app.services.source_adapters.errors import (
    AdapterConfigurationError,
    AdapterDisabledError,
    AdapterResponseError,
)
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "careerjet_search_response.json"

_ENABLED_SETTINGS = Settings(
    source_careerjet_enabled=True,
    source_careerjet_base_url="https://search.api.careerjet.net/v4/query",
    source_careerjet_api_key="test-api-key-abc",
)


class GetFixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def get_json(self, url, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)

    def post_json(self, url, *, body=None, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        raise AssertionError("Careerjet не должен использовать post_json")


def _load_fixture() -> object:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _expected_basic_auth(api_key: str) -> str:
    return "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()


def test_careerjet_adapter_maps_response_into_common_preview_contract() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(
        SourceSearchInput(query="lager", location="Berlin", radius_km=25, page=1, page_size=10)
    )

    call = transport.calls[0]
    assert call["url"] == "https://search.api.careerjet.net/v4/query"
    # Германия задаётся жёстко в адаптере
    assert call["params"]["location"] == "Deutschland"
    assert call["params"]["locale_code"] == "de_DE"
    assert call["params"]["keywords"] == "lager"
    # user_ip обязателен по документации Careerjet API v4 (geo-targeting).
    # Это IP конечного пользователя, НЕ инструмент авторизации (авторизация — по outbound IP).
    # Без реального user_ip в запросе используется fallback "127.0.0.1".
    assert "user_ip" in call["params"]
    assert call["params"]["user_ip"] == "127.0.0.1"  # fallback: search_input.user_ip=None
    assert "user_agent" in call["params"]
    # fragment_size берётся из настроек (по умолчанию 500)
    assert call["params"]["fragment_size"] == 500
    # radius передаётся когда задан (25 в запросе)
    assert call["params"]["radius"] == 25
    # Basic Auth
    assert call["headers"]["Authorization"] == _expected_basic_auth("test-api-key-abc")

    assert response.source_id == "careerjet"
    assert response.total_count == 87
    # Третья запись (без url) должна быть пропущена
    assert len(response.records) == 2
    assert len(response.warnings) == 1
    assert "пропущено 1" in response.warnings[0]

    first = response.records[0]
    # external_id = "cj-" + md5(url)[:16]
    expected_id = "cj-" + hashlib.md5(b"https://jobviewtrack.com/v2/job-cj-de-1001").hexdigest()[:16]
    assert first.external_id == expected_id
    # source_reference хранит оригинальный URL
    assert first.source_reference == "https://jobviewtrack.com/v2/job-cj-de-1001"
    assert first.title == "Lagermitarbeiter (m/w/d)"
    assert first.company == "Logistik Nord GmbH"
    assert first.location == "München, Bayern"
    assert first.posted_at == "Sun, 19 Apr 2026 09:00:00 GMT"
    assert first.detail_url == "https://jobviewtrack.com/v2/job-cj-de-1001"
    assert first.raw_payload["salary"] == "€2600"


def test_careerjet_adapter_germany_locale_is_default() -> None:
    """Germany/local mode searches Deutschland regardless of form location noise."""
    transport = GetFixtureTransport({"type": "JOBS", "hits": 0, "jobs": []})
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="helfer", location="France"))

    params = transport.calls[0]["params"]
    assert params["location"] == "Deutschland"
    assert params["locale_code"] == "de_DE"


def test_careerjet_adapter_remote_worldwide_uses_remote_location() -> None:
    transport = GetFixtureTransport({"type": "JOBS", "hits": 0, "jobs": []})
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="python", location="remote", search_mode="remote_worldwide"))

    params = transport.calls[0]["params"]
    assert params["location"] == "Remote"


def test_careerjet_adapter_does_not_send_radius_when_not_provided() -> None:
    """Если radius_km не задан, параметр radius не отправляется."""
    transport = GetFixtureTransport({"type": "JOBS", "hits": 0, "jobs": []})
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="lager"))  # radius_km=None по умолчанию

    assert "radius" not in transport.calls[0]["params"]


def test_careerjet_adapter_passes_real_user_ip_when_provided() -> None:
    """Если user_ip задан в search_input, он передаётся в запрос как есть."""
    transport = GetFixtureTransport({"type": "JOBS", "hits": 0, "jobs": []})
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="lager", user_ip="88.155.34.141"))

    params = transport.calls[0]["params"]
    assert params["user_ip"] == "88.155.34.141"
    # Must NOT be a hardcoded registered server IP constant
    assert params["user_ip"] != "127.0.0.1"


def test_careerjet_adapter_uses_fallback_ip_when_user_ip_is_none() -> None:
    """Без реального user_ip используется явный fallback "127.0.0.1"."""
    transport = GetFixtureTransport({"type": "JOBS", "hits": 0, "jobs": []})
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(SourceSearchInput(query="lager"))  # user_ip=None по умолчанию

    assert transport.calls[0]["params"]["user_ip"] == "127.0.0.1"


def test_careerjet_adapter_raises_when_api_key_not_configured() -> None:
    settings = Settings(source_careerjet_enabled=True, source_careerjet_api_key=None)
    adapter = CareerjetAdapter(settings=settings, http_transport=GetFixtureTransport({}))

    with pytest.raises(AdapterConfigurationError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert "SOURCE_CAREERJET_API_KEY" in exc_info.value.message


def test_careerjet_adapter_raises_when_disabled() -> None:
    settings = Settings(source_careerjet_enabled=False, source_careerjet_api_key="key")
    adapter = CareerjetAdapter(settings=settings, http_transport=GetFixtureTransport({}))

    with pytest.raises(AdapterDisabledError):
        adapter.search(SourceSearchInput(query="lager"))


def test_careerjet_adapter_handles_locations_mode_gracefully() -> None:
    """API возвращает LOCATIONS если локация неоднозначна — адаптер не падает."""
    payload = {
        "type": "LOCATIONS",
        "locations": ["Berlin, Germany", "Berlin, USA"],
        "message": "multiple locations found",
    }
    transport = GetFixtureTransport(payload)
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="lager"))

    assert len(response.records) == 0
    assert response.total_count == 0
    assert any("multiple locations found" in w for w in response.warnings)


def test_careerjet_adapter_raises_for_non_list_jobs() -> None:
    transport = GetFixtureTransport({"type": "JOBS", "hits": 0, "jobs": "broken"})
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="lager"))

    assert "jobs" in exc_info.value.message


def test_careerjet_adapter_raw_payload_is_preserved() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="lager"))

    assert response.raw_payload == payload


def test_careerjet_adapter_external_id_is_deterministic_hash_of_url() -> None:
    """Один и тот же URL всегда даёт один и тот же external_id."""
    url = "https://jobviewtrack.com/v2/some-job"
    expected = "cj-" + hashlib.md5(url.encode()).hexdigest()[:16]

    payload = {
        "type": "JOBS",
        "hits": 1,
        "jobs": [{"title": "Test", "company": "Co", "url": url, "locations": "Berlin"}],
    }
    transport = GetFixtureTransport(payload)
    adapter = CareerjetAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="test"))

    assert response.records[0].external_id == expected
