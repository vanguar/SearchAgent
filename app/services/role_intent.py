"""Public API for multilingual role-intent normalization.

Lexicon data (RoleIntent type, ROLE_INTENT_MAP, sorted keys) lives in
_role_intent_lexicon.py to keep this module focused on matching logic only.
"""
from __future__ import annotations

from app.services._role_intent_lexicon import (  # noqa: F401  (re-export RoleIntent)
    IT_WORD_RE,
    ROLE_INTENT_MAP,
    RoleIntent,
    SORTED_INTENT_KEYS,
)

__all__ = ["RoleIntent", "normalize_role_intent"]


def normalize_role_intent(query: str) -> RoleIntent | None:
    """Translate a raw user query into a RoleIntent via greedy longest-match."""
    normalized = query.strip().lower()
    if not normalized:
        return None
    for key in SORTED_INTENT_KEYS:
        if key == "it":
            if IT_WORD_RE.search(normalized):
                return ROLE_INTENT_MAP[key]
        elif key in normalized:
            return ROLE_INTENT_MAP[key]
    return None
