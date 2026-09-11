from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.services.source_adapters.errors import AdapterRequestError, HttpTransportError
from app.services.source_adapters.hh_adapter import HHAdapter
from app.services.source_adapters.http import HttpJsonResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class FixtureTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.areas_payload = json.loads((FIXTURES / "hh_areas_response.json").read_text(encoding="utf-8"))
        self.vacancies_payload = json.loads((FIXTURES / "hh_vacancies_response.json").read_text(encoding="utf-8"))
        self.areas_payloads_by_host: dict[str, object] = {}

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        if url.endswith("/areas"):
            return HttpJsonResponse(
                url=url,
                status_code=200,
                payload=self.areas_payloads_by_host.get(_host_from_url(url), self.areas_payload),
            )
        return HttpJsonResponse(url=url, status_code=200, payload=self.vacancies_payload)


class ForbiddenVacanciesTransport(FixtureTransport):
    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        if url.endswith("/areas"):
            return HttpJsonResponse(url=url, status_code=200, payload=self.areas_payload)
        raise HttpTransportError(
            url=url,
            status_code=403,
            message='{"errors":[{"type":"forbidden"}],"request_id":"test"}',
        )


def test_hh_adapter_resolves_country_area_ids_before_searching() -> None:
    transport = FixtureTransport()
    settings = Settings(source_hh_enabled=True, source_hh_country_names="Kazakhstan,Kyrgyzstan,Uzbekistan,Georgia,Moldova")
    adapter = HHAdapter(settings=settings, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="python developer", page=1, page_size=25))

    area_calls = [call for call in transport.calls if str(call["url"]).endswith("/areas")]
    assert {call["url"] for call in area_calls} == {
        "https://api.hh.kz/areas",
        "https://api.headhunter.kg/areas",
        "https://api.hh.uz/areas",
        "https://api.headhunter.ge/areas",
        "https://api.hh.ru/areas",
    }
    vacancy_calls = [call for call in transport.calls if str(call["url"]).endswith("/vacancies")]
    assert {call["params"]["area"] for call in vacancy_calls} == {"40", "48", "97", "28"}
    assert all(call["params"]["text"] == "python developer" for call in vacancy_calls)
    assert all(call["params"]["per_page"] == 25 for call in vacancy_calls)
    assert all(call["params"]["page"] == 0 for call in vacancy_calls)
    assert response.source_id == "hh"
    assert response.total_count == 8
    assert len(response.records) == 1
    assert any("Moldova" in warning for warning in response.warnings)


def test_hh_adapter_resolves_localized_country_area_names() -> None:
    transport = FixtureTransport()
    localized_areas = [
        {"id": "40", "name": "Казахстан", "areas": []},
        {"id": "48", "name": "Кыргызстан", "areas": []},
        {"id": "97", "name": "Узбекистан", "areas": []},
        {"id": "28", "name": "Грузия", "areas": []},
        {"id": "1001", "name": "Молдова", "areas": []},
    ]
    transport.areas_payload = localized_areas
    transport.areas_payloads_by_host = {
        "api.hh.kz": localized_areas,
        "api.headhunter.kg": localized_areas,
        "api.hh.uz": localized_areas,
        "api.headhunter.ge": localized_areas,
    }
    settings = Settings(source_hh_enabled=True, source_hh_country_names="Kazakhstan,Kyrgyzstan,Uzbekistan,Georgia,Moldova")
    adapter = HHAdapter(settings=settings, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="python developer", page=1, page_size=25))

    vacancy_calls = [call for call in transport.calls if str(call["url"]).endswith("/vacancies")]
    assert {call["params"]["area"] for call in vacancy_calls} == {"40", "48", "97", "28", "1001"}
    assert response.warnings == ()


def test_hh_adapter_uses_regional_api_hosts_for_supported_countries() -> None:
    transport = FixtureTransport()
    settings = Settings(source_hh_enabled=True, source_hh_country_names="Kazakhstan,Kyrgyzstan,Uzbekistan,Georgia,Moldova")
    adapter = HHAdapter(settings=settings, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="python developer", page=1, page_size=25))

    vacancy_urls_by_area = {
        call["params"]["area"]: call["url"]
        for call in transport.calls
        if str(call["url"]).endswith("/vacancies")
    }
    assert vacancy_urls_by_area == {
        "40": "https://api.hh.kz/vacancies",
        "48": "https://api.headhunter.kg/vacancies",
        "97": "https://api.hh.uz/vacancies",
        "28": "https://api.headhunter.ge/vacancies",
    }
    assert response.raw_payload["countries"]["Kazakhstan"]["base_url"] == "https://api.hh.kz"
    assert response.raw_payload["countries"]["Kyrgyzstan"]["base_url"] == "https://api.headhunter.kg"
    assert response.raw_payload["countries"]["Uzbekistan"]["base_url"] == "https://api.hh.uz"
    assert response.raw_payload["countries"]["Georgia"]["base_url"] == "https://api.headhunter.ge"


class PartiallyForbiddenVacanciesTransport(FixtureTransport):
    """403 только для одного хоста — остальные страны продолжают отвечать."""

    def __init__(self, forbidden_host: str) -> None:
        super().__init__()
        self.forbidden_host = forbidden_host

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        if url.endswith("/vacancies") and _host_from_url(url) == self.forbidden_host:
            self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
            raise HttpTransportError(
                url=url,
                status_code=403,
                message='{"errors":[{"type":"forbidden"}],"request_id":"test"}',
            )
        return super().get_json(url, params=params, headers=headers, timeout_seconds=timeout_seconds)


def test_hh_adapter_turns_one_forbidden_country_into_a_warning() -> None:
    """Частичный отказ не должен ронять источник — остальные страны отдают вакансии."""
    transport = PartiallyForbiddenVacanciesTransport("api.hh.kz")
    settings = Settings(source_hh_enabled=True, source_hh_country_names="Kazakhstan,Kyrgyzstan")
    adapter = HHAdapter(settings=settings, http_transport=transport)

    response = adapter.search(SourceSearchInput(query="python developer", page=1, page_size=25))

    assert response.records != ()
    assert response.warnings == (
        "HH: доступ к поиску вакансий для Kazakhstan на https://api.hh.kz заблокирован API (HTTP 403 forbidden).",
    )


def test_hh_adapter_raises_when_every_country_is_forbidden() -> None:
    """Полный отказ обязан быть ошибкой: пустой ответ неотличим от «ничего не нашлось»."""
    transport = ForbiddenVacanciesTransport()
    settings = Settings(source_hh_enabled=True, source_hh_country_names="Kazakhstan,Kyrgyzstan")
    adapter = HHAdapter(settings=settings, http_transport=transport)

    with pytest.raises(AdapterRequestError) as exc_info:
        adapter.search(SourceSearchInput(query="python developer", page=1, page_size=25))

    assert exc_info.value.status_code == 403
    assert "Kazakhstan" in exc_info.value.message
    assert "Kyrgyzstan" in exc_info.value.message


def test_hh_adapter_never_uses_russia_or_belarus_from_config() -> None:
    transport = FixtureTransport()
    settings = Settings(source_hh_enabled=True, source_hh_country_names="Russia,Belarus,Kazakhstan")
    adapter = HHAdapter(settings=settings, http_transport=transport)

    adapter.search(SourceSearchInput(query="backend", page=2, page_size=10))

    vacancy_calls = [call for call in transport.calls if str(call["url"]).endswith("/vacancies")]
    assert len(vacancy_calls) == 1
    assert vacancy_calls[0]["params"]["area"] == "40"
    assert vacancy_calls[0]["params"]["page"] == 1


def test_hh_adapter_maps_vacancy_fields_to_preview_contract() -> None:
    transport = FixtureTransport()
    adapter = HHAdapter(settings=Settings(source_hh_enabled=True, source_hh_country_names="Kazakhstan"), http_transport=transport)

    response = adapter.search(SourceSearchInput(query="python"))

    record = response.records[0]
    assert record.external_id == "112233"
    assert record.title == "Python Backend Developer"
    assert record.company == "Tech KZ"
    assert record.location == "Almaty"
    assert record.posted_at == "2026-05-18"
    assert record.detail_url == "https://hh.kz/vacancy/112233"
    assert record.raw_payload["salary"] == "300000-500000 KZT"
    assert record.raw_payload["country"] == "Kazakhstan"


def _host_from_url(url: str) -> str:
    return url.split("/", 3)[2]
