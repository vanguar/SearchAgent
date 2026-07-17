from __future__ import annotations

from typing import Protocol

from app.services.normalization_models import CanonicalVacancyGroup
from app.services.search_models import VacancySignalSnapshot
from app.services.translation_service import TranslationService


class SummaryHelper(Protocol):
    """Optional helper boundary for short vacancy summaries."""

    def summarize(self, text: str) -> str | None:
        """Return a short Russian summary or None."""


class SummaryService:
    """Short Russian summaries with a deterministic local fallback."""

    def __init__(
        self,
        *,
        translation_service: TranslationService | None = None,
        helper: SummaryHelper | None = None,
    ) -> None:
        self.translation_service = translation_service or TranslationService()
        self.helper = helper
        # Per-instance cache keyed by body text — dedupes LLM summary calls within one run.
        self._llm_cache: dict[str, str | None] = {}

    def build_summary(
        self,
        canonical: CanonicalVacancyGroup,
        signals: VacancySignalSnapshot,
        *,
        translated_title_ru: str | None = None,
        use_llm: bool = True,
    ) -> str | None:
        if use_llm and self.helper is not None and canonical.source_records:
            body_text = canonical.source_records[0].body_text or ""
            if body_text in self._llm_cache:
                cached = self._llm_cache[body_text]
                if cached:
                    return cached
            else:
                helper_summary = self.helper.summarize(body_text)
                normalized_summary = helper_summary.strip() if helper_summary else None
                self._llm_cache[body_text] = normalized_summary
                if normalized_summary:
                    return normalized_summary

        translated_title = translated_title_ru or self.translation_service.translate_title(
            normalized_title=canonical.normalized_title,
            original_title=canonical.source_records[0].original_title if canonical.source_records else None,
            use_llm=use_llm,
        )
        if translated_title is None and canonical.source_records:
            translated_title = canonical.source_records[0].original_title

        facts: list[str] = []
        if signals.low_language_signal:
            facts.append("языковой барьер невысокий")
        elif signals.strong_german_required:
            facts.append("нужен хороший немецкий")
        if signals.shift_signal:
            facts.append("есть смены")
        if signals.immediate_start_signal:
            facts.append("можно быстро выйти")
        if signals.entry_level_signal:
            facts.append("без упора на опыт")
        if signals.relocation_signal:
            facts.append("есть помощь с жильем")

        if translated_title and facts:
            return f"{translated_title}. {', '.join(facts[:2]).capitalize()}."
        if translated_title:
            return translated_title
        if facts:
            return f"{', '.join(facts[:2]).capitalize()}."
        return None
