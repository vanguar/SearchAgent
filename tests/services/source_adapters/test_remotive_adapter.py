import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.source_adapters.errors import AdapterDisabledError, AdapterResponseError
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput
from app.services.source_adapters.remotive_adapter import RemotiveAdapter, _is_germany_compatible

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "remotive_jobs_response.json"

_ENABLED_SETTINGS = Settings(
    source_remotive_enabled=True,
    source_remotive_base_url="https://remotive.com/api/remote-jobs",
)


class GetFixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def get_json(self, url, *, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpJsonResponse(url=url, status_code=200, payload=self.payload)

    def post_json(self, url, *, body=None, params=None, headers=None, timeout_seconds=10.0) -> HttpJsonResponse:
        raise AssertionError("Remotive не должен использовать post_json")


def _load_fixture() -> object:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# --- Тесты фильтра Germany-compatible ---

@pytest.mark.parametrize("location,expected", [
    # Явно совместимые
    ("Germany", True),
    ("Deutschland", True),
    ("Germany, Austria, Switzerland", True),
    ("Europe", True),
    ("European Union", True),
    ("EU", True),
    ("EMEA", True),
    ("DACH", True),
    ("dach", True),
    ("Germany or Europe", True),
    # Не совместимые — шум глобального рынка
    ("Worldwide", False),
    ("worldwide", False),
    ("Global", False),
    ("Anywhere", False),
    ("Remote", False),
    ("International", False),
    # Пустая / None — неизвестно, исключаем
    ("", False),
    (None, False),
    # Явно не Германия
    ("USA Only", False),
    ("North America", False),
    ("Latin America", False),
    ("Australia", False),
    ("UK Only", False),
    ("Asia", False),
    ("Canada", False),
])
def test_is_germany_compatible(location: str | None, expected: bool) -> None:
    assert _is_germany_compatible(location) is expected


# --- Тесты адаптера ---

def test_remotive_adapter_maps_response_and_filters_non_germany() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="logistics", page=1, page_size=10))

    assert transport.calls[0]["url"] == "https://remotive.com/api/remote-jobs"
    assert transport.calls[0]["params"]["search"] == "logistics"

    assert response.source_id == "remotive"
    # "USA Only" (5003) и "Worldwide" (5004) оба фильтруются → остаётся 2 из 4
    assert len(response.records) == 2
    assert response.total_count == 2

    ids = {r.external_id for r in response.records}
    assert "5001" in ids
    assert "5002" in ids
    assert "5003" not in ids
    assert "5004" not in ids

    assert any("отфильтровано 2" in w for w in response.warnings)


def test_remotive_adapter_record_fields() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="logistics", page=1, page_size=10))

    first = next(r for r in response.records if r.external_id == "5001")
    assert first.title == "Remote Logistics Coordinator"
    assert first.company == "GlobalShip GmbH"
    assert first.location == "Germany"
    assert first.posted_at == "2026-04-18T10:00:00"
    assert first.detail_url == "https://remotive.com/remote-jobs/5001"
    assert first.raw_payload["salary"] == "3000-4000 EUR"


def test_remotive_adapter_applies_manual_pagination() -> None:
    """Remotive не поддерживает серверную пагинацию, адаптер пагинирует сам."""
    # 2 вакансии, совместимых с Германией; page=2, page_size=1 → вторая запись
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="logistics", page=2, page_size=1))

    assert len(response.records) == 1
    assert response.total_count == 2


def test_remotive_adapter_includes_worldwide_for_remote_search() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(
        SourceSearchInput(
            query="logistics",
            location="remote",
            search_mode="remote_worldwide",
            page=1,
            page_size=10,
        )
    )

    assert len(response.records) == 4
    assert response.total_count == 4
    assert {record.external_id for record in response.records} == {"5001", "5002", "5003", "5004"}
    assert "limit" not in transport.calls[0]["params"]


def test_remotive_adapter_remote_worldwide_does_not_apply_manual_page_size_limit() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(
        SourceSearchInput(
            query="logistics",
            location="remote",
            search_mode="remote_worldwide",
            page=1,
            page_size=1,
        )
    )

    assert len(response.records) == 4
    assert response.total_count == 4


def test_remotive_adapter_germany_mode_ignores_stale_remote_location() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(
        SourceSearchInput(
            query="logistics",
            location="remote",
            search_mode="germany_local",
            page=1,
            page_size=10,
        )
    )

    assert len(response.records) == 2
    assert response.total_count == 2
    assert {record.external_id for record in response.records} == {"5001", "5002"}


def test_remotive_adapter_keeps_exact_it_search_query() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    adapter.search(
        SourceSearchInput(
            query="Python Backend Developer",
            location="remote",
            search_mode="remote_worldwide",
            page=1,
            page_size=10,
        )
    )

    params = transport.calls[0]["params"]
    assert params["search"] == "Python Backend Developer"
    assert "category" not in params


def test_remotive_adapter_raises_when_disabled() -> None:
    adapter = RemotiveAdapter(
        settings=Settings(source_remotive_enabled=False),
        http_transport=GetFixtureTransport({}),
    )
    with pytest.raises(AdapterDisabledError):
        adapter.search(SourceSearchInput(query="logistics"))


def test_remotive_adapter_raises_for_non_list_jobs() -> None:
    transport = GetFixtureTransport({"job-count": 0, "jobs": "broken"})
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    with pytest.raises(AdapterResponseError) as exc_info:
        adapter.search(SourceSearchInput(query="logistics"))

    assert "jobs" in exc_info.value.message


def test_remotive_adapter_raw_payload_is_preserved() -> None:
    payload = _load_fixture()
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="logistics"))

    assert response.raw_payload == payload


def test_remotive_adapter_skips_records_without_id() -> None:
    payload = {
        "jobs": [
            {
                "url": "https://remotive.com/5099",
                "title": "No ID job",
                "company_name": "Acme",
                "candidate_required_location": "Germany",
            }
        ]
    }
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="lager"))

    assert len(response.records) == 0
    assert any("пропущено 1" in w for w in response.warnings)


def test_remotive_adapter_excludes_empty_location() -> None:
    """Пустая локация теперь исключается (неизвестно откуда)."""
    payload = {
        "jobs": [
            {"id": 9001, "title": "Unknown Location Job", "company_name": "X",
             "candidate_required_location": "", "url": "https://remotive.com/9001"},
            {"id": 9002, "title": "No Location Field", "company_name": "Y",
             "url": "https://remotive.com/9002"},
        ]
    }
    transport = GetFixtureTransport(payload)
    adapter = RemotiveAdapter(settings=_ENABLED_SETTINGS, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="test"))

    assert len(response.records) == 0
    assert any("отфильтровано 2" in w for w in response.warnings)
