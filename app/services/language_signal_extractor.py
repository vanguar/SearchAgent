from __future__ import annotations

import re

from app.services.hashers import normalize_text_for_fingerprint
from app.services.normalization_models import LanguageSignals

_STRONG_GERMAN_PATTERNS = (
    r"\b(?:sehr gute|gute|fliessend(?:e|er|es|en)?|verhandlungssicher(?:e|er|es|en)?|sichere|b1|b2|c1|c2)\s+deutsch",
    r"\bdeutsch(?:kenntnisse)?\s+(?:mindestens\s+)?(?:b1|b2|c1|c2)\b",
    r"\bdeutschkenntnisse\b(?!\s*.{0,30}\b(?:nicht|keine)\s+(?:erforderlich|notwendig)\b).{0,80}\b(?:erforderlich|vorausgesetzt|zwingend|required|mandatory)\b",
    # «Deutsch in Wort und Schrift» и «fliessend Deutsch» — уверенный уровень,
    # а не просто наличие требования.
    r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdeutsch\s+in\s+wort\s+und\s+schrift\b",
    r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdeutsche\s+sprache\s+in\s+wort\s+und\s+schrift\b",
    r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\b(?:du\s+sprichst|sie\s+sprechen)\s+flie(?:ss|s)end\s+deutsch\b",
    r"\bgerman\b.*\b(?:required|must|mandatory)\b",
)
_LOW_LANGUAGE_PATTERNS = (
    r"\bohne deutsch",
    r"\bkeine deutschkenntnisse",
    r"\b(?:deutsch|deutschkenntnisse)\s+nicht\s+(?:erforderlich|notwendig)\b",
    r"\bgerman\s+(?:is\s+)?not\s+(?:required|necessary|mandatory)\b",
    r"\b(?:kein|keine|no)\s+(?:deutsch|deutschkenntnisse|german)\s+(?:erforderlich|required|necessary)\b",
    r"\bgrundkenntnisse(?: in deutsch)?\b",
    r"\beinfache deutschkenntnisse\b",
    r"\b(?:deutsch|deutschkenntnisse)\s+(?:auf\s+)?a1(?:\s+niveau)?\b",
    r"\ba1(?:\s+niveau)?\s+(?:deutsch|deutschkenntnisse)\b",
    r"\bbasic german\b",
    r"\benglish only\b",
)
# Как требование по английскому пишут на самом деле. Набор проверен на 100 реальных
# объявлениях Djinni: прежние четыре паттерна находили 9 требований на 43 упоминания —
# они знали только "b2/c1/c2" и связку "english ... required". В IT-объявлениях уровень
# пишут словами ("upper intermediate"), а обязательность не пишут вовсе: "good written
# and spoken English" в разделе требований и есть требование.
#
# Паттерны общие для LanguageSignalExtractor и rule_catalog.ENGLISH_REQUIRED_RULE:
# объявлены здесь один раз и импортируются, раньше существовали в двух копиях.
ENGLISH_REQUIRED_PATTERNS: tuple[str, ...] = (
    r"\benglish\b.{0,50}\b(?:required|mandatory|essential|must have|hard requirement)\b",
    r"\b(?:fluent|business|professional|advanced|excellent|strong|solid|confident)\s+english\b",
    r"\benglish\s+(?:proficiency|fluency)\b",
    r"\b(?:proficiency|fluency|command)\s+(?:in|of)\s+english\b",
    # Уровень по CEFR: и "english b2", и "english level b2", и "english at b2 level".
    r"\benglish\b.{0,25}\b(?:b1|b2|c1|c2)\b",
    r"\b(?:b1|b2|c1|c2)\b.{0,25}\benglish\b",
    # Уровень словами — в IT-объявлениях это основная форма записи.
    r"\b(?:upper[\s-]?intermediate|pre[\s-]?intermediate|intermediate|advanced|proficient)\b.{0,25}\benglish\b",
    r"\benglish\b.{0,25}\b(?:upper[\s-]?intermediate|pre[\s-]?intermediate|intermediate|advanced|proficient)\b",
    # Требование без слова "required": названа сама рабочая функция на языке.
    r"\b(?:written|spoken|verbal|oral)\b.{0,45}\benglish\b",
    r"\benglish\b.{0,45}\b(?:communication skills|written and spoken|written and verbal)\b",
    r"\bmust\b.{0,40}\b(?:speak|write|communicate in) english\b",
)
_ENGLISH_REQUIRED_PATTERNS = ENGLISH_REQUIRED_PATTERNS

ENGLISH_PREFERRED_PATTERNS: tuple[str, ...] = (
    r"\benglish\b.{0,30}\b(?:preferred|a plus|an asset|nice to have|desirable|advantage)\b",
    r"\b(?:preferred|desirable)\b.{0,20}\benglish\b",
)
_ENGLISH_PREFERRED_PATTERNS = ENGLISH_PREFERRED_PATTERNS

# Английский как СОЦПАКЕТ, а не требование: "english courses with a native speaker",
# "english classes and educational events", "speaking clubs to maintain your english".
# Без этой проверки корпоративные плюшки читались бы как языковой барьер.
ENGLISH_BENEFIT_PATTERNS: tuple[str, ...] = (
    r"\benglish\s+(?:courses?|classes|lessons?|clubs?|school|training|tutor\w*)\b",
    r"\b(?:courses?|classes|lessons?|clubs?)\b.{0,30}\benglish\b",
    r"\b(?:maintain|improve|develop|practice|practise)\b.{0,25}\benglish\b",
    r"\benglish\b.{0,30}\bnative speaker\b",
    r"\b(?:compensation|reimbursement|coverage)\b.{0,30}\benglish\b",
)
_SHIFT_PATTERNS = (
    r"\bschicht",
    r"\bschichtarbeit",
    r"\bnachtarbeit",
    r"\bwochenend",
    r"\b3 schicht",
)
_HELPER_ROLE_PATTERNS = (
    r"\bhelfer\b",
    r"\bmitarbeiter\b",
    r"\blager",
    r"\bproduktion",
    r"\bverpacker",
    r"\bkommissionier",
    r"\bsortier",
    r"\bwarehouse\b",
    r"\bpacking\b",
)


class LanguageSignalExtractor:
    """Extract deterministic language and role signals from title/body text."""

    def extract(self, *, title: str | None, body_text: str | None) -> LanguageSignals:
        combined = normalize_text_for_fingerprint(" ".join(part for part in (title, body_text) if part))

        return LanguageSignals(
            strong_german_required=_matches_any(combined, _STRONG_GERMAN_PATTERNS),
            german_mentioned=bool(re.search(r"\b(?:deutsch\w*|german)\b", combined)),
            english_mentioned=bool(re.search(r"\b(?:englisch\w*|english)\b", combined)),
            english_required=_matches_any(combined, _ENGLISH_REQUIRED_PATTERNS),
            english_preferred=_matches_any(combined, _ENGLISH_PREFERRED_PATTERNS),
            low_language_signal=_matches_any(combined, _LOW_LANGUAGE_PATTERNS),
            shift_signal=_matches_any(combined, _SHIFT_PATTERNS),
            helper_role_signal=_matches_any(combined, _HELPER_ROLE_PATTERNS),
        )


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
