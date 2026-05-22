from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.source_adapters.dou_rss_adapter import DouRssAdapter
from app.services.source_adapters.errors import AdapterRequestError, HttpTransportError
from app.services.source_adapters.http import HttpTextResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "dou_rss_response.xml"


class FixtureTextTransport:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    def get_text(self, url: str, *, params=None, headers=None, timeout_seconds: float = 10.0) -> HttpTextResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpTextResponse(url=url, status_code=200, text=self.text)


class CloudflareDeniedTransport:
    def get_text(self, url: str, *, params=None, headers=None, timeout_seconds: float = 10.0) -> HttpTextResponse:
        raise HttpTransportError(
            url=url,
            status_code=403,
            message="<html><title>Access denied | jobs.dou.ua used Cloudflare</title>Error 1010</html>",
        )


def test_dou_rss_adapter_reads_category_feed_and_maps_records() -> None:
    transport = FixtureTextTransport(FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = DouRssAdapter(settings=Settings(), text_transport=transport)

    response = adapter.search(SourceSearchInput(query="Python Backend", page=1, page_size=10))

    assert transport.calls[0]["url"] == "https://jobs.dou.ua/vacancies/feeds/"
    assert transport.calls[0]["params"] == {"category": "Python"}
    assert response.source_id == "dou_rss"
    assert len(response.records) == 1
    record = response.records[0]
    assert record.title == "Python Developer at Precoro"
    assert record.external_id == "https://jobs.dou.ua/companies/precoro/vacancies/123/"
    assert record.posted_at == "2026-05-18"
    assert record.detail_url == "https://jobs.dou.ua/companies/precoro/vacancies/123/"


def test_dou_rss_adapter_uses_search_param_when_no_known_category_matches() -> None:
    transport = FixtureTextTransport(FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = DouRssAdapter(settings=Settings(), text_transport=transport)

    adapter.search(SourceSearchInput(query="FastAPI automation"))

    assert transport.calls[0]["params"] == {"search": "FastAPI automation"}


def test_dou_rss_adapter_reports_cloudflare_1010_without_html_dump() -> None:
    adapter = DouRssAdapter(settings=Settings(), text_transport=CloudflareDeniedTransport())

    try:
        adapter.search(SourceSearchInput(query="python"))
    except AdapterRequestError as exc:
        assert "Cloudflare" in exc.message
        assert "Error 1010" in exc.message
        assert "<html>" not in exc.message
        assert exc.retryable is False
    else:
        raise AssertionError("Expected AdapterRequestError")
