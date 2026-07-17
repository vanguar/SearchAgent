from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.search_models import FilterResult, RelevanceBand, ScoreResult

if TYPE_CHECKING:
    from app.services.llm_client import OpenAILLMClient


class MatchExplainer:
    """Короткие русские объяснения для карточек вакансий."""

    def __init__(self, *, llm_client: OpenAILLMClient | None = None) -> None:
        self._llm_client = llm_client

    def explain(
        self,
        *,
        filter_result: FilterResult,
        score_result: ScoreResult,
        relevance_band: RelevanceBand = "high",
    ) -> str:
        """Детерминированное объяснение — всегда работает без LLM."""
        positive_labels = _labels(filter_result.positive_hits, score_result.positive_hits)
        negative_labels = _labels(filter_result.rejection_hits, filter_result.review_hits, score_result.negative_hits)

        if filter_result.hard_reject:
            reasons = negative_labels[:2] or ["слишком высокий барьер по требованиям"]
            return f"Скорее не подходит: {', '.join(reasons)}."

        if filter_result.review_required:
            parts: list[str] = []
            if positive_labels:
                parts.append(positive_labels[0])
            for label in negative_labels:
                if label not in parts:
                    parts.append(label)
                if len(parts) == 3:
                    break
            if not parts:
                parts.append("есть несколько спорных требований")
            return f"С осторожностью: {', '.join(parts)}."

        reasons = positive_labels[:3] or ["базово выглядит реалистично"]
        if relevance_band == "medium":
            return f"Смежная роль: {', '.join(reasons)}."
        return f"Подходит: {', '.join(reasons)}."

    def explain_with_feedback_note(
        self,
        *,
        filter_result: FilterResult,
        score_result: ScoreResult,
        relevance_band: RelevanceBand = "high",
        feedback_note_ru: str | None = None,
    ) -> str:
        """Deterministic explanation with optional appended feedback note."""
        base = self.explain(
            filter_result=filter_result,
            score_result=score_result,
            relevance_band=relevance_band,
        )
        if feedback_note_ru:
            return f"{base} {feedback_note_ru}"
        return base

    def explain_from_body(
        self,
        *,
        filter_result: FilterResult,
        score_result: ScoreResult,
        body_text: str | None = None,
        relevance_band: RelevanceBand = "high",
    ) -> str:
        """
        Объяснение с опциональным LLM-уточнением на основе текста вакансии.

        Использовать для детальной карточки вакансии — не в массовом поиске.
        При отсутствии LLM или ошибке провайдера возвращает детерминированный результат.
        """
        base = self.explain(filter_result=filter_result, score_result=score_result, relevance_band=relevance_band)
        if self._llm_client is None or not body_text:
            return base
        enriched = self._llm_client.explain_match(
            deterministic_explanation=base,
            body_text=body_text,
        )
        return enriched if enriched else base


def _labels(*collections) -> list[str]:  # type: ignore[no-untyped-def]
    labels: list[str] = []
    for collection in collections:
        for hit in collection:
            if hit.label_ru in labels:
                continue
            labels.append(hit.label_ru)
    return labels
