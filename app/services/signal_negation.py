"""Bounded negation of a signal, retaining sentence/list boundaries.

Only explicit negation is removed. A separate positive occurrence still counts;
an unrelated requirement in the next clause cannot negate the current one.
"""
from __future__ import annotations

import re

from app.services.hashers import normalize_text_for_fingerprint

_BOUNDARY = re.compile(r"[.;!?\n]|,(?!\d)|\b(?:aber|jedoch|sondern|but)\b", re.I)
_PREFIX = re.compile(
    r"\b(?:kein\w*|ohne|weder|statt|no|without)\s+"
    r"(?:(?:gute\w*|eigene\w*|besondere\w*|einsatz|fahrt\w*|fahrzeug\w*|mit|auf|als|uber|ab)\s+){0,3}$"
    r"|\banstelle\s+von\s+$|\bnicht\s+(?:mit|auf|als)\s+$"
)
_SUFFIX = re.compile(
    r"^\s*(?:(?:erfahrung|kenntnisse|kenntnissen|schein|niveau|b[12]|c[12])\s+)?"
    r"(?:(?:ist|sind|wird|werden|is|are)\s+)?"
    r"(?:nicht|not)\s+(?:zwingend\s+)?"
    r"(?:notig|notwendig|erforderlich|vorausgesetzt|benotigt|required|necessary|mandatory)\b"
)


def normalize_signal_text(text: str | None) -> str:
    """Fold accents like fingerprinting, but keep clause boundaries as semicolons."""
    return "; ".join(normalize_text_for_fingerprint(part) for part in _BOUNDARY.split(text or ""))


def is_negated_signal(text: str, match: re.Match[str]) -> bool:
    start = text.rfind(";", 0, match.start()) + 1
    end = text.find(";", match.end())
    prefix = text[max(start, match.start() - 90):match.start()]
    suffix = text[match.end():end if end >= 0 else len(text)]
    # Double negation and emphasis do not waive a requirement.
    if re.search(r"\bnicht\s+ohne\s*$", prefix):
        return False
    return bool(_PREFIX.search(prefix) or _SUFFIX.search(suffix))


def mask_negated_signals(text: str, pattern: re.Pattern[str]) -> str:
    """Remove only negated mentions, retaining offsets and every other signal."""
    chars = list(text)
    for match in pattern.finditer(text):
        if is_negated_signal(text, match):
            chars[match.start():match.end()] = " " * (match.end() - match.start())
    return "".join(chars)
