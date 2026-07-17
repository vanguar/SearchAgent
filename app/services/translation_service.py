from __future__ import annotations

from typing import Protocol

from app.services.hashers import normalize_text_for_fingerprint


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
            if candidate in self._llm_cache:
                cached = self._llm_cache[candidate]
                if cached:
                    return cached
            else:
                translated = self.helper.translate_title(candidate)
                normalized_translated = translated.strip() if translated else None
                self._llm_cache[candidate] = normalized_translated
                if normalized_translated:
                    return normalized_translated

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
