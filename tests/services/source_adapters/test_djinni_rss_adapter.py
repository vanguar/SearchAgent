from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.source_adapters.djinni_rss_adapter import (
    DJINNI_PRIMARY_KEYWORDS,
    DjinniRssAdapter,
    resolve_djinni_primary_keywords,
)
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


def test_djinni_skips_query_that_maps_to_no_rubric() -> None:
    """Запрос вне словаря рубрик не должен уходить в Djinni.

    Djinni на неизвестный primary_keyword отдаёт общий нефильтрованный фид, а не ошибку,
    поэтому единственный способ не подмешать посторонние вакансии — не спрашивать вовсе.
    """
    transport = FixtureTextTransport(FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = DjinniRssAdapter(settings=Settings(), text_transport=transport)

    response = adapter.search(SourceSearchInput(query="Lagerarbeiter", page=1, page_size=10))

    assert transport.calls == []
    assert response.records == ()
    assert response.total_count == 0
    assert response.warnings and "не соответствует ни одной рубрике" in response.warnings[0]


def test_djinni_ai_query_asks_both_rubrics_and_dedupes() -> None:
    transport = FixtureTextTransport(FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = DjinniRssAdapter(settings=Settings(), text_transport=transport)

    response = adapter.search(SourceSearchInput(query="AI Automation Specialist", page=1, page_size=10))

    assert [call["params"] for call in transport.calls] == [
        {"primary_keyword": "Data Science"},
        {"primary_keyword": "Python"},
    ]
    # Обе рубрики отдали одну и ту же запись фикстуры — в выдаче она должна быть одна.
    assert len(response.records) == 1
    assert response.warnings and "нет своей рубрики" in response.warnings[0]


def test_djinni_empty_query_still_reads_unfiltered_feed() -> None:
    transport = FixtureTextTransport(FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = DjinniRssAdapter(settings=Settings(), text_transport=transport)

    response = adapter.search(SourceSearchInput(query="", page=1, page_size=10))

    assert transport.calls[0]["params"] == {}
    assert len(response.records) == 1


def test_djinni_keyword_resolution_covers_profile_terms() -> None:
    assert resolve_djinni_primary_keywords("Python Developer") == ("Python",)
    assert resolve_djinni_primary_keywords("FastAPI") == ("Python",)
    assert resolve_djinni_primary_keywords("Senior Java Developer") == ("Java",)
    assert resolve_djinni_primary_keywords("QA Automation") == ("QA Automation",)
    assert resolve_djinni_primary_keywords("специалист по нейросетям") == ("Data Science", "Python")
    assert resolve_djinni_primary_keywords("Fahrer Klasse B") == ()
    assert resolve_djinni_primary_keywords(None) == ()


def test_djinni_rubric_aliases_point_at_real_rubrics() -> None:
    """Любой алиас обязан указывать на рубрику из проверенного словаря."""
    for keywords in (
        resolve_djinni_primary_keywords(term)
        for term in ("AI Agent Builder", "Prompt Engineer", "Node.js", "Data Science", "SEO")
    ):
        for keyword in keywords:
            assert keyword in DJINNI_PRIMARY_KEYWORDS
