"""Проверка машинного перевода перед тем, как показать его человеку.

Модель иногда сбивается на китайский посреди фразы («Водитель по搬搬 мебели»,
«交通和安全规定») и путает числа: «Arztpraxen - 14,25 €/h» превращалось в
«14-25 ч». Такой перевод хуже, чем никакого: человек верит карточке.

Правила простые и детерминированные:
* в русском тексте не должно быть иероглифов (CJK);
* каждое число из оригинала должно остаться в переводе;
* в пересказе не должно появляться чисел, которых нет в исходном тексте.
"""
from __future__ import annotations

import re

from app.core.relevance_config import RelevanceConfig, get_relevance_config

# Иероглифы и слоговые азбуки: CJK, хирагана, катакана, хангыль, полноширинные формы.
_CJK_RE = re.compile(
    "[⺀-⿟　-ヿ㄀-ㇿ㐀-䶿一-鿿가-힯豈-﫿＀-￯]"
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def has_cjk(text: str | None) -> bool:
    return bool(text and _CJK_RE.search(text))


def numbers_in(text: str | None) -> set[str]:
    """Числа текста в одной записи: «14,25» и «14.25» совпадают, «1.000» = «1000»."""
    found: set[str] = set()
    for raw in _NUMBER_RE.findall(text or ""):
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
            found.add(raw.replace(".", ""))
        else:
            found.add(raw.replace(",", "."))
    return found


def is_valid_title_translation(original: str, translated: str | None) -> bool:
    if not translated or not translated.strip() or has_cjk(translated):
        return False
    return numbers_in(original) <= numbers_in(translated)


def is_valid_summary(source_text: str, summary: str | None) -> bool:
    if not summary or not summary.strip() or has_cjk(summary):
        return False
    return numbers_in(summary) <= numbers_in(source_text)


def glossary_prompt_block(config: RelevanceConfig | None = None) -> str:
    """Строки глоссария для промпта: «Botenfahrer = курьер-водитель»."""
    resolved = config or get_relevance_config()
    return "\n".join(f"- {term} = {meaning}" for term, meaning in resolved.translation_glossary)
