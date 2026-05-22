from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.source_adapters.djinni_rss_adapter import DjinniRssAdapter
from app.services.source_adapters.http import HttpTextResponse
from app.services.source_adapters.models import SourceSearchInput

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "djinni_rss_response.xml"


class FixtureTextTransport:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    def get_text(self, url: str, *, params=None, headers=None, timeout_seconds: float = 10.0) -> HttpTextResponse:
        self.calls.append({"url": url, "params": params or {}})
        return HttpTextResponse(url=url, status_code=200, text=self.text)


def test_djinni_rss_adapter_reads_feed_and_maps_records() -> None:
    transport = FixtureTextTransport(FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = DjinniRssAdapter(settings=Settings(), text_transport=transport)

    response = adapter.search(SourceSearchInput(query="Python", page=1, page_size=10))

    assert transport.calls[0]["url"] == "https://djinni.co/jobs/rss/"
    assert transport.calls[0]["params"] == {"primary_keyword": "Python"}
    assert response.source_id == "djinni_rss"
    assert len(response.records) == 1
    record = response.records[0]
    assert record.title == "Backend Python Developer"
    assert record.external_id == "djinni-123"
    assert record.posted_at == "2026-05-19"
    assert record.detail_url == "https://djinni.co/jobs/123-backend-python-developer/"
