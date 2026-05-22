"""Тесты адаптера Adzuna — контракт SourceRecordPreview и обработка ошибок."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.source_adapters.adzuna_adapter import AdzunaAdapter
from app.services.source_adapters.errors import (
    AdapterConfigurationError,
    AdapterRequestError,
    AdapterResponseError,
)
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "adzuna_search_response.json"


class FixtureTransport:
    """Транспорт-заглушка, возвращающий переданный payload без реального HTTP."""

    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict[str, object]] = []

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpJsonResponse(url=url, status_code=self.status_code, payload=self.payload)


class SequencedTransport:
    """Транспорт-заглушка, возвращающий payload по порядку вызовов."""

    def __init__(self, payloads: list[object], *, status_code: int = 200) -> None:
        self.payloads = payloads
        self.status_code = status_code
        self.calls: list[dict[str, object]] = []

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}})
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return HttpJsonResponse(url=url, status_code=self.status_code, payload=payload)


def _load_fixture() -> object:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _settings_with_keys() -> Settings:
    """Settings с ключами Adzuna. Берём из окружения (реальные из .env или тестовые)."""
    import os
    # Если .env уже загружен — используем его значения; иначе ставим тестовые.
    if not os.environ.get("ADZUNA_APP_ID"):
        os.environ["ADZUNA_APP_ID"] = "test_app_id"
    if not os.environ.get("ADZUNA_APP_KEY"):
        os.environ["ADZUNA_APP_KEY"] = "test_app_key"
    return Settings(source_adzuna_enabled=True)


# ---------------------------------------------------------------------------
# Базовый контракт адаптера
# ---------------------------------------------------------------------------

def test_adzuna_adapter_maps_response_to_common_preview_contract() -> None:
    """Adzuna ответ корректно маппится в SourceRecordPreview."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    response = adapter.search(
        SourceSearchInput(query="lager", location="Berlin", radius_km=25, page=1, page_size=10)
    )

    # URL должен содержать страницу как часть пути
    assert "/1" in transport.calls[0]["url"]
    params = transport.calls[0]["params"]
    assert params.get("what") == "lager"
    assert params.get("where") == "Berlin"
    assert params.get("distance") == 25

    assert response.source_id == "adzuna"
    assert response.source_name == "Adzuna"
    assert response.total_count == 128
    assert len(response.records) == 3
    assert response.page == 1
    assert response.page_size == 10


def test_adzuna_adapter_maps_first_record_fields() -> None:
    """Первая запись корректно содержит все поля из фикстуры."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    response = adapter.search(SourceSearchInput(query="lager", page=1, page_size=10))

    first = response.records[0]
    assert first.external_id == "4618529301"
    assert first.source_reference == "4618529301"
    assert first.title == "Lagermitarbeiter (m/w/d)"
    assert first.company == "Logistik Nord GmbH"
    assert first.location == "Berlin, Deutschland"
    assert first.posted_at == "2026-04-15"  # только дата из ISO строки
    assert first.detail_url == "https://www.adzuna.de/jobs/details/4618529301"
    # salary_text формируется из salary_min/salary_max
    assert first.raw_payload.get("salary") == "2200–2600 EUR"


def test_adzuna_adapter_salary_min_only() -> None:
    """salary_min без salary_max → 'от N EUR'."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    response = adapter.search(SourceSearchInput(query="produktion", page=1, page_size=10))

    # Третья запись — salary_min=2100, salary_max=null
    third = response.records[2]
    assert third.raw_payload.get("salary") == "от 2100 EUR"


def test_adzuna_adapter_passes_credentials_in_params() -> None:
    """app_id и app_key передаются в query-параметрах запроса (не пустые)."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    settings = _settings_with_keys()
    adapter = AdzunaAdapter(settings=settings, http_transport=transport)

    adapter.search(SourceSearchInput(query="lager", page=1, page_size=5))

    params = transport.calls[0]["params"]
    assert params.get("app_id") == settings.adzuna_app_id
    assert params.get("app_key") == settings.adzuna_app_key
    assert params.get("results_per_page") == 5


def test_adzuna_adapter_page_embedded_in_url_path() -> None:
    """Страница 3 → URL содержит .../3 как часть пути, не как query-параметр."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    adapter.search(SourceSearchInput(query="lager", page=3, page_size=10))

    assert transport.calls[0]["url"].endswith("/3")


def test_adzuna_adapter_no_location_in_query_when_not_provided() -> None:
    """Если location не задан, параметр 'where' не передаётся."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    adapter.search(SourceSearchInput(query="lager", location=None, page=1, page_size=10))

    params = transport.calls[0]["params"]
    assert "where" not in params


def test_adzuna_adapter_splits_city_list_into_separate_requests() -> None:
    """Adzuna не понимает 'Rostock, Stralsund' как список, поэтому ищем города отдельно."""
    payload_rostock = {
        "count": 1,
        "results": [
            {
                "id": "rostock-1",
                "title": "Zusteller Rostock",
                "location": {"display_name": "Rostock"},
                "redirect_url": "https://example.de/rostock-1",
            }
        ],
    }
    payload_stralsund = {
        "count": 1,
        "results": [
            {
                "id": "stralsund-1",
                "title": "Zusteller Stralsund",
                "location": {"display_name": "Stralsund"},
                "redirect_url": "https://example.de/stralsund-1",
            }
        ],
    }
    transport = SequencedTransport([payload_rostock, payload_stralsund])
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    response = adapter.search(
        SourceSearchInput(query="zusteller", location="Rostock, Stralsund", radius_km=50, page=1, page_size=10)
    )

    assert [call["params"]["where"] for call in transport.calls] == ["Rostock", "Stralsund"]
    assert response.total_count == 2
    assert [record.external_id for record in response.records] == ["rostock-1", "stralsund-1"]
    assert any("отдельный поиск" in warning for warning in response.warnings)


def test_adzuna_adapter_keeps_city_country_location_as_single_place() -> None:
    """'Berlin, Deutschland' — это один город с страной, а не список городов."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    adapter.search(SourceSearchInput(query="lager", location="Berlin, Deutschland", page=1, page_size=10))

    assert len(transport.calls) == 1
    assert transport.calls[0]["params"].get("where") == "Berlin, Deutschland"


def test_adzuna_adapter_omits_remote_as_location_for_remote_worldwide_mode() -> None:
    """Remote mode is search semantics; Adzuna `where` must stay a real place."""
    payload = _load_fixture()
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    adapter.search(
        SourceSearchInput(
            query="python developer",
            location="remote",
            search_mode="remote_worldwide",
            page=1,
            page_size=10,
        )
    )

    params = transport.calls[0]["params"]
    assert params.get("what") == "python developer"
    assert "where" not in params


# ---------------------------------------------------------------------------
# Конфигурационные ошибки
# ---------------------------------------------------------------------------

def test_adzuna_adapter_raises_configuration_error_when_no_credentials() -> None:
    """Без ключей адаптер бросает AdapterConfigurationError."""
    import os
    # Временно убираем ключи из окружения
    saved_id = os.environ.pop("ADZUNA_APP_ID", None)
    saved_key = os.environ.pop("ADZUNA_APP_KEY", None)
    try:
        settings = Settings(source_adzuna_enabled=True)
        adapter = AdzunaAdapter(settings=settings, http_transport=FixtureTransport({}))
        with pytest.raises(AdapterConfigurationError):
            adapter.search(SourceSearchInput(query="lager", page=1, page_size=5))
    finally:
        if saved_id is not None:
            os.environ["ADZUNA_APP_ID"] = saved_id
        if saved_key is not None:
            os.environ["ADZUNA_APP_KEY"] = saved_key


# ---------------------------------------------------------------------------
# Ошибки HTTP-транспорта
# ---------------------------------------------------------------------------

def test_adzuna_adapter_wraps_transport_error_as_adapter_request_error() -> None:
    """Ошибка HTTP → AdapterRequestError (не пропускает сырое исключение)."""
    from app.services.source_adapters.errors import HttpTransportError

    class ErrorTransport:
        def get_json(self, url: str, **kwargs: object) -> HttpJsonResponse:
            raise HttpTransportError(url=url, message="Connection refused", status_code=503)

    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=ErrorTransport())  # type: ignore[arg-type]

    with pytest.raises(AdapterRequestError) as exc_info:
        adapter.search(SourceSearchInput(query="lager", page=1, page_size=5))

    assert exc_info.value.source_id == "adzuna"
    assert "Adzuna" in exc_info.value.message


def test_adzuna_adapter_wraps_invalid_json_as_adapter_response_error() -> None:
    """Невалидный JSON → AdapterResponseError."""
    from app.services.source_adapters.errors import HttpDecodeError

    class BadJsonTransport:
        def get_json(self, url: str, **kwargs: object) -> HttpJsonResponse:
            raise HttpDecodeError(url=url, message="Response body is not valid JSON.")

    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=BadJsonTransport())  # type: ignore[arg-type]

    with pytest.raises(AdapterResponseError):
        adapter.search(SourceSearchInput(query="lager", page=1, page_size=5))


def test_adzuna_adapter_raises_response_error_on_unexpected_format() -> None:
    """Ответ — не объект (например, строка) → AdapterResponseError."""
    transport = FixtureTransport("unexpected string response")
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    with pytest.raises(AdapterResponseError):
        adapter.search(SourceSearchInput(query="lager", page=1, page_size=5))


# ---------------------------------------------------------------------------
# Обработка частично невалидных записей
# ---------------------------------------------------------------------------

def test_adzuna_adapter_skips_records_without_id() -> None:
    """Записи без поля id пропускаются, счётчик skipped отражается в warnings."""
    payload = {
        "count": 2,
        "results": [
            {
                "id": "111",
                "title": "Lager",
                "company": {"display_name": "GmbH"},
                "location": {"display_name": "Berlin"},
                "created": "2026-04-15T00:00:00Z",
                "redirect_url": "https://example.de/1",
            },
            {
                # нет id — должна быть пропущена
                "title": "Ohne ID",
                "company": {"display_name": "X"},
            },
        ],
    }
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    response = adapter.search(SourceSearchInput(query="lager", page=1, page_size=10))

    assert len(response.records) == 1
    assert response.records[0].external_id == "111"
    assert any("пропущено" in w for w in response.warnings)


def test_adzuna_adapter_handles_missing_optional_fields_gracefully() -> None:
    """Записи без company/location/salary не бросают исключений."""
    payload = {
        "count": 1,
        "results": [
            {
                "id": "999",
                "title": "Minimal Job",
                "created": "2026-04-15T00:00:00Z",
                "redirect_url": "https://example.de/999",
            }
        ],
    }
    transport = FixtureTransport(payload)
    adapter = AdzunaAdapter(settings=_settings_with_keys(), http_transport=transport)

    response = adapter.search(SourceSearchInput(query="job", page=1, page_size=10))

    assert len(response.records) == 1
    record = response.records[0]
    assert record.company is None
    assert record.location is None
    assert record.raw_payload.get("salary") is None
