"""Minimized public RSS vacancies from the reported cards, verified 2026-10-06."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.services.ai_tools_profile import AI_TOOLS_DESIRED_ROLES, AI_TOOLS_SEARCH_QUERY_TERMS
from app.services.search_models import SearchProfileContext
from app.services.search_service import SearchService
from app.services.source_adapters.djinni_rss_adapter import DjinniRssAdapter, _company_from_description
from app.services.source_adapters.dou_rss_adapter import DouRssAdapter
from app.services.source_adapters.http import HttpTextResponse
from app.services.source_adapters.models import SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.web.views import templates

CASES = json.loads((Path(__file__).parents[1] / "fixtures/rss_card_regressions.json").read_text(encoding="utf-8"))
PROFILE = SearchProfileContext(
    profile_label="AI automation", profile_source="saved", work_authorized=True,
    german_level="A1", english_level="A2", desired_roles=AI_TOOLS_DESIRED_ROLES,
    search_query_terms=AI_TOOLS_SEARCH_QUERY_TERMS,
)


class FixtureTransport:
    def __init__(self, case: dict, description: str | None = None):
        body = case["description"] if description is None else description
        self.xml = (
            f'<rss><channel><item><title>{case["title"]}</title><link>{case["link"]}</link>'
            f'<guid>{case["link"]}</guid><description><![CDATA[{body}]]></description>'
            '<pubDate>Tue, 06 Oct 2026 10:00:00 +0300</pubDate></item></channel></rss>'
        )

    def get_text(self, url, **kwargs):
        return HttpTextResponse(url=url, status_code=200, text=self.xml)


def adapter_for(case, description=None):
    adapter_type = DjinniRssAdapter if case["source_id"] == "djinni_rss" else DouRssAdapter
    return adapter_type(
        settings=Settings(source_djinni_enabled=True, source_dou_enabled=True),
        text_transport=FixtureTransport(case, description),
    )


def search(adapters, profile=PROFILE):
    return SearchService(
        settings=Settings(openai_api_key=None),
        registry=SourceAdapterRegistry(adapters=adapters),
        profile_resolver=SimpleNamespace(resolve=lambda **kwargs: profile),
    ).search(
        search_input=SourceSearchInput(query="AI Automation Specialist", search_mode="remote_worldwide"),
        source_ids=tuple(adapter.source_id for adapter in adapters), enrich_with_llm=False,
    )


def render(result):
    return templates.env.get_template("jobs/partials/search_results.html").render(search_result=result)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["source_id"])
def test_full_rss_body_preserves_payload_and_company(case):
    record = adapter_for(case).search(SourceSearchInput(query="AI Automation Specialist")).records[0]
    assert record.description_complete is True
    assert record.company == case["company"]
    assert record.raw_payload["description"] == case["description"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["source_id"])
@pytest.mark.parametrize("description", ["", "<p>&nbsp;</p>", "Brief job description.", "A detailed description " * 25 + "..."])
def test_empty_short_or_clipped_rss_keeps_insufficient_warning(case, description):
    adapter = adapter_for(case, description)
    record = adapter.search(SourceSearchInput(query="AI Automation Specialist")).records[0]
    assert record.description_complete is False
    # A senior result is intentionally hidden for the candidate; use an
    # unrelated profession profile to inspect the body signal independently.
    profile = replace(PROFILE, desired_roles=("Senior Python Developer",), search_query_terms=())
    result = search([adapter], profile)
    assert result.results[0].signals.description_insufficient is True


@pytest.mark.parametrize("description,company", [
    ("<p><strong>Медіабай</strong>&nbsp;шукає Python Developer.</p>", "Медіабай"),
    ("Компания «Медіабай» ищет Python Developer.", "Медіабай"),
    ("Company Example Studio is hiring an AI developer.", "Example Studio"),
    ("Ми шукаємо Python Developer. Працюємо з Медіабай.", None),
    ("Our company is hiring a Python Developer.", None),
    ("Ми шукає Python Developer.", None),
    ("Backend Developer. Медіабай шукає партнерів.", None),
    ("Компанія шукає розробника.", None),
])
def test_company_requires_explicit_named_employer_introduction(description, company):
    assert _company_from_description(description) == company


def test_reported_cards_through_search_and_rendering():
    result = search([adapter_for(case) for case in CASES])
    assert len(result.maybe_results) == 1
    backend = result.maybe_results[0]
    assert backend.primary_record.original_company == "Медіабай"
    assert backend.signals.description_insufficient is False
    assert any(hit.code == "it_experience_gap" for hit in backend.filter_result.review_hits)
    senior = next(item for item in result.hidden_filtered_items if "Senior AI Business Systems Analyst" in item.title)
    assert any(hit.code == "it_seniority_gap" for hit in senior.rejection_reasons)
    assert not any("Senior AI Business Systems Analyst" in item.primary_record.original_title for item in result.hot_results)
    html = render(result)
    assert '<input type="hidden" name="company_name" value="Медіабай"' in html
    assert "Компания не указана" not in html
    assert "Данных недостаточно" not in html
    assert "Python/Go Backend Developer (AI-driven)" in html


def test_clipped_backend_card_still_renders_warning():
    html = render(search([adapter_for(CASES[0], "Медіабай шукає Python Developer. Claude Code, REST API...")]))
    assert "Данных недостаточно" in html
