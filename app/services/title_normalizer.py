from __future__ import annotations

import re

from app.services.hashers import normalize_text_for_fingerprint

_BRACKET_NOISE_RE = re.compile(
    r"\((?:[^)]*\b(?:m/?w/?d|w/?m/?d|d/?m/?w|mwd|mwdiv|gn|all genders?)\b[^)]*)\)",
    re.IGNORECASE,
)
# Любая запись пола из 2–4 букв через «/»: (w/m/x/d), m/f/d, (m/w/x).
_GENDER_SLASH_RE = re.compile(r"\(?(?<![\w/])[mwdfx](?:\s*/\s*[mwdfx]){1,3}(?![\w/])\)?", re.IGNORECASE)
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
        cleaned = _GENDER_SLASH_RE.sub(" ", cleaned)
        cleaned = _GENDER_ENDING_RE.sub(r"\g<stem>", cleaned)
        cleaned = _INLINE_NOISE_RE.sub(" ", cleaned)
        cleaned = cleaned.replace("/", " ").replace("-", " ")
        return normalize_text_for_fingerprint(cleaned)
