from __future__ import annotations

from pathlib import Path

import pytest
from app.core.config import Settings
from app.services.source_adapters.dou_rss_adapter import DouRssAdapter, parse_dou_title
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


@pytest.mark.parametrize(
    ("title", "role", "company", "location"),
    [
        ("AI Engineer в Atlas Technica, віддалено", "AI Engineer", "Atlas Technica", "віддалено"),
        (
            "Solution Lead for AI Tools; ID 102481 в SoftServe, Київ, Харків, віддалено",
            "Solution Lead for AI Tools; ID 102481",
            "SoftServe",
            "Київ, Харків, віддалено",
        ),
        ("Python Developer в Elementica, $1500–3500, Ужгород", "Python Developer", "Elementica", "Ужгород"),
        ("Strong Junior/Middle DevOps", "Strong Junior/Middle DevOps", None, None),
    ],
)
def test_dou_title_carries_company_and_location(
    title: str, role: str, company: str | None, location: str | None
) -> None:
    """DOU кладёт всё в заголовок; отдельных полей в фиде нет.

    Без разбора каждая карточка показывала «Компания не указана», а локации не
    было вовсе — то есть отфильтровать выдачу по географии было нечем.
    """
    parts = parse_dou_title(title)

    assert parts.role == role
    assert parts.company == company
    assert parts.location == location


def test_dou_title_splits_on_the_last_preposition() -> None:
    """Предлог "в" встречается и внутри самой роли."""
    parts = parse_dou_title(
        "Backend Developer, розробник систем логістики, військовослужбовець в 13 бригада НГУ, $595–1900"
    )

    assert parts.company == "13 бригада НГУ"
    assert parts.role.startswith("Backend Developer")
    assert parts.location is None
