from __future__ import annotations

import re

from app.services.hashers import normalize_text_for_fingerprint

_BRACKET_NOISE_RE = re.compile(
    r"\((?:[^)]*\b(?:m/?w/?d|w/?m/?d|d/?m/?w|mwd|mwdiv|gn|all genders?)\b[^)]*)\)",
    re.IGNORECASE,
)
_INLINE_NOISE_RE = re.compile(
    r"\b(?:m/?w/?d|w/?m/?d|d/?m/?w|mwd|mwdiv|gn|all genders?|gesucht|ab sofort|sofort|vollzeit|teilzeit|remote|hybrid)\b",
    re.IGNORECASE,
)
_GENDER_ENDING_RE = re.compile(r"(?P<stem>[A-Za-zÄÖÜäöüß]+)(?:/in|:in|\*in|/innen|:innen|\*innen)\b")


class TitleNormalizer:
    """Normalize vacancy titles for deterministic matching."""

    def normalize(self, title: str | None) -> str:
        if not title:
            return ""

        cleaned = title.replace("|", " ").replace("+", " ").replace("_", " ")
        cleaned = _BRACKET_NOISE_RE.sub(" ", cleaned)
        cleaned = _GENDER_ENDING_RE.sub(r"\g<stem>", cleaned)
        cleaned = _INLINE_NOISE_RE.sub(" ", cleaned)
        cleaned = cleaned.replace("/", " ").replace("-", " ")
        return normalize_text_for_fingerprint(cleaned)
