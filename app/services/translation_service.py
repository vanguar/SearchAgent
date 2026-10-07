from __future__ import annotations

import re
from typing import Protocol

from app.core.relevance_config import get_relevance_config
from app.services.hashers import normalize_text_for_fingerprint
from app.services.translation_quality import is_valid_title_translation

# Пометка пола в заголовке переводу не нужна и только сбивает модель.
_GENDER_MARKER_RE = re.compile(
    r"\(\s*(?:[mwdfx]\s*/\s*){1,3}[mwdfx]\s*\)|\((?:gn|all genders?)\)", re.IGNORECASE
)


class TranslationHelper(Protocol):
    """Optional helper boundary for short title translation."""

    def translate_title(self, text: str) -> str | None:
        """Return a short translated title or None."""


_KNOWN_TITLE_TRANSLATIONS = {
    "lagermitarbeiter": "Сотрудник склада",
    "lagerhelfer": "Помощник на складе",
    "produktionshelfer": "Помощник на производстве",
    "produktionsmitarbeiter": "Сотрудник производства",
    "verpacker": "Упаковщик",
    "kommissionierer": "Комплектовщик",
    "sortierer": "Сортировщик",
    "logistikmitarbeiter": "Сотрудник логистики",
    "montagemitarbeiter": "Сотрудник сборки",
    "maschinenbediener": "Оператор оборудования",
}
_TOKEN_TRANSLATIONS = {
    "lager": "склад",
    "warehouse": "склад",
    "mitarbeiter": "сотрудник",
    "helfer": "помощник",
    "produktion": "производство",
    "produktions": "производство",
    "verpack": "упаковка",
    "verpacker": "упаковщик",
    "kommissionierer": "комплектовщик",
    "sortierer": "сортировщик",
    "logistik": "логистика",
    "montage": "сборка",
}


class TranslationService:
    """Local-safe translation fallback for short vacancy labels."""

    def _checked_llm_title(self, title: str) -> str | None:
        """Перевод модели, прошедший проверку, иначе исходный заголовок.

        В модель уходит ИСХОДНЫЙ заголовок, а не нормализованный: в
        нормализованном «14,25 €/h» становится «14 25 h», и модель честно
        переводила это как «14-25 ч». Ответ с иероглифами или с потерянными
        числами повторяется, а после повторов заменяется исходным заголовком.
        """
        assert self.helper is not None
        source = _GENDER_MARKER_RE.sub(" ", title)
        source = " ".join(source.split())
        answered = False
        for _ in range(1 + max(0, get_relevance_config().translation_retries)):
            translated = self.helper.translate_title(source)
            normalized_translated = translated.strip() if translated else None
            if normalized_translated is None:
                break
            answered = True
            if is_valid_title_translation(source, normalized_translated):
                return normalized_translated
        # Модель молчит — дальше словарный перевод. Модель ответила мусором —
        # честнее показать исходный немецкий заголовок.
        return (source or None) if answered else None

    def __init__(self, *, helper: TranslationHelper | None = None) -> None:
        self.helper = helper
        # Per-instance cache to avoid re-calling the LLM for identical titles within one
        # search run (the service is constructed per request).
        self._llm_cache: dict[str, str | None] = {}

    def translate_title(
        self,
        *,
        normalized_title: str,
        original_title: str | None = None,
        use_llm: bool = True,
    ) -> str | None:
        candidate = normalize_text_for_fingerprint(normalized_title or original_title)
        if not candidate:
            return None

        if use_llm and self.helper is not None:
            if candidate not in self._llm_cache:
                self._llm_cache[candidate] = self._checked_llm_title(original_title or normalized_title)
            cached = self._llm_cache[candidate]
            if cached:
                return cached

        direct = _KNOWN_TITLE_TRANSLATIONS.get(candidate)
        if direct:
            return direct

        translated_tokens = [_TOKEN_TRANSLATIONS.get(token) for token in candidate.split()]
        translated_tokens = [token for token in translated_tokens if token]
        if not translated_tokens:
            return None

        if len(translated_tokens) == 2 and translated_tokens[0] == "склад" and translated_tokens[1] == "сотрудник":
            return "Сотрудник склада"
        if len(translated_tokens) == 2 and translated_tokens[0] == "производство" and translated_tokens[1] == "сотрудник":
            return "Сотрудник производства"

        return " ".join(translated_tokens).capitalize()
