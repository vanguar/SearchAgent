from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(text: str | None) -> str:
    if text is None:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def ascii_fold(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def normalize_text_for_fingerprint(text: str | None) -> str:
    folded = ascii_fold(text).casefold()
    cleaned = _NON_ALNUM_RE.sub(" ", folded)
    return normalize_whitespace(cleaned)


def fingerprint_tokens(text: str | None, *, stopwords: Iterable[str] = ()) -> tuple[str, ...]:
    normalized = normalize_text_for_fingerprint(text)
    if not normalized:
        return ()

    stopword_set = {normalize_text_for_fingerprint(value) for value in stopwords}
    tokens: list[str] = []
    seen: set[str] = set()
    for token in normalized.split():
        if len(token) < 2 or token in stopword_set or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def stable_hash(text: str | None) -> str:
    normalized = normalize_text_for_fingerprint(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def combine_hash_parts(*parts: str | None) -> str:
    joined = " || ".join(normalize_text_for_fingerprint(part) for part in parts if part)
    return stable_hash(joined)


def token_similarity(tokens_a: tuple[str, ...], tokens_b: tuple[str, ...]) -> float:
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union
