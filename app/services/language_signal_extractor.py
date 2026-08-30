from __future__ import annotations

import re

from app.services.hashers import normalize_text_for_fingerprint
from app.services.normalization_models import LanguageSignals

_STRONG_GERMAN_PATTERNS = (
    r"\b(?:sehr gute|gute|fliessend(?:e|er|es|en)?|verhandlungssicher(?:e|er|es|en)?|sichere|b1|b2|c1|c2)\s+deutsch",
    r"\bdeutsch(?:kenntnisse)?\s+(?:mindestens\s+)?(?:b1|b2|c1|c2)\b",
    r"\bdeutschkenntnisse\b(?!\s*.{0,30}\b(?:nicht|keine)\s+(?:erforderlich|notwendig)\b).{0,80}\b(?:erforderlich|vorausgesetzt|zwingend|required|mandatory)\b",
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
            low_language_signal=_matches_any(combined, _LOW_LANGUAGE_PATTERNS),
            shift_signal=_matches_any(combined, _SHIFT_PATTERNS),
            helper_role_signal=_matches_any(combined, _HELPER_ROLE_PATTERNS),
        )


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
