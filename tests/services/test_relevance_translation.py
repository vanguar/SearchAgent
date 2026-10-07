"""Проблема 10: перевод без иероглифов, с сохранёнными числами и глоссарием."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.services.llm_client import OpenAILLMClient
from app.services.summary_service import SummaryService
from app.services.translation_quality import has_cjk, numbers_in
from app.services.translation_service import TranslationService
from tests.services.relevance_support import build_canonical, courier_run, flatten_cards


class _SloppyTranslator:
    """Модель, которая ошибается, как в живой выдаче: иероглифы и потерянные числа."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def translate_title(self, text: str) -> str | None:
        self.calls.append(text)
        attempt = self.calls.count(text)
        if "Möbel" in text or "Umzüge" in text:
            return "Водитель по搬搬 мебели"
        if attempt == 1 and any(char.isdigit() for char in text):
            return "Водитель 14-25 ч"
        return "Водитель " + " ".join(sorted(numbers_in(text)))


def test_title_with_cjk_falls_back_to_the_original_german_title() -> None:
    service = TranslationService(helper=_SloppyTranslator())

    result = service.translate_title(normalized_title="betriebsfahrer umzuge mobel", original_title="Betriebsfahrer Umzüge / Möbel")

    assert result == "Betriebsfahrer Umzüge / Möbel"


def test_lost_numbers_trigger_a_retry_with_the_original_title() -> None:
    helper = _SloppyTranslator()
    service = TranslationService(helper=helper)

    result = service.translate_title(
        normalized_title="fahrer arztpraxen 14 25 h", original_title="Fahrer - Arztpraxen - 14,25 €/h (m/w/d)"
    )

    assert helper.calls[0] == "Fahrer - Arztpraxen - 14,25 €/h"
    assert len(helper.calls) == 2
    assert result is not None and "14.25" in numbers_in(result)


def test_summary_with_cjk_or_invented_numbers_uses_deterministic_fallback() -> None:
    helper = MagicMock()
    helper.summarize.side_effect = ["Соблюдать 交通和安全规定.", "Оплата 99 € в час."]
    service = SummaryService(helper=helper)
    canonical = build_canonical(
        title="Kurierfahrer (m/w/d)",
        body="Wir suchen Kurierfahrer für Berlin. Bezahlung 14,25 € pro Stunde, Vollzeit, ab sofort möglich. " * 2,
    )
    from app.services.rule_catalog import inspect_vacancy
    from tests.services.relevance_support import courier_profile

    summary = service.build_summary(canonical, inspect_vacancy(canonical, courier_profile()), use_llm=True)

    assert summary is not None
    assert not has_cjk(summary)
    assert "99" not in summary
    assert helper.summarize.call_count == 2


@pytest.mark.parametrize("term", ["Botenfahrer", "Stückgut", "Kommissioniertätigkeit", "Liliengewächse", "Abrufkraft"])
def test_glossary_is_part_of_translation_and_summary_prompts(term: str) -> None:
    client = OpenAILLMClient.__new__(OpenAILLMClient)
    client._call = MagicMock(return_value="ок")  # type: ignore[method-assign]

    client.translate_title("Botenfahrer Spedition Stückgut")
    client.summarize("Kommissioniertätigkeit und Fahrten. " * 10)

    prompts = " ".join(call.args[0] for call in client._call.call_args_list)
    assert term in prompts


def test_translations_of_the_real_run_have_no_cjk_and_keep_numbers() -> None:
    result = courier_run()
    titles = {
        card.primary_record.original_title
        for card in flatten_cards((*result.hot_results, *result.maybe_results, *result.rejected_results))
    }
    service = TranslationService(helper=_SloppyTranslator())

    for title in titles:
        translated = service.translate_title(normalized_title=title.casefold(), original_title=title)
        assert translated is not None
        assert not has_cjk(translated), title
        assert numbers_in(title) <= numbers_in(translated), (title, translated)
