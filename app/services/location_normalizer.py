from __future__ import annotations

import re

from app.services.hashers import fingerprint_tokens, normalize_text_for_fingerprint, normalize_whitespace
from app.services.normalization_models import NormalizedLocation

_POSTAL_CODE_RE = re.compile(r"\b\d{5}\b")
_CITY_PREFIX_RE = re.compile(r"^(?:raum|region|nahe|naehe|bei)\s+", re.IGNORECASE)
# Full-word country names, matched case-insensitively on normalized text.
_COUNTRY_WORD_ALIASES: dict[str, tuple[str, ...]] = {
    "DE": ("deutschland", "germany"),
    "AT": ("oesterreich", "osterreich", "austria"),
    "CH": ("schweiz", "switzerland"),
    "PL": ("polen", "poland"),
    "NL": ("niederlande", "netherlands"),
}
# Two-letter ISO codes are matched ONLY when they appear uppercase in the raw text.
# Lower-casing them (as before) made the English preposition "at" resolve to Austria,
# "de"/"in" to a country, etc. — a real misclassification for remote/English locations.
_COUNTRY_ISO_CODES: tuple[str, ...] = ("DE", "AT", "CH", "PL", "NL")


class LocationNormalizer:
    """Normalize practical Germany-style location strings."""

    def normalize(self, location_text: str | None) -> NormalizedLocation:
        raw_text = normalize_whitespace(location_text)
        if not raw_text:
            return NormalizedLocation(raw_text=None, normalized_text=None)

        country_code = self._detect_country_code(raw_text)
        postal_code_match = _POSTAL_CODE_RE.search(raw_text)
        postal_code = postal_code_match.group(0) if postal_code_match else None

        display_city = self._extract_city(raw_text, postal_code=postal_code)
        normalized_city = normalize_text_for_fingerprint(display_city) if display_city else ""
        normalized_text = ", ".join(part for part in (normalized_city or None, country_code) if part) or None

        token_source = " ".join(part for part in (display_city, country_code, postal_code) if part)
        return NormalizedLocation(
            raw_text=raw_text,
            normalized_text=normalized_text,
            country_code=country_code,
            city=display_city,
            postal_code=postal_code,
            tokens=fingerprint_tokens(token_source),
        )

    def _detect_country_code(self, location_text: str) -> str | None:
        # Uppercase ISO code in the raw text (e.g. "Vienna, AT") — case-sensitive on purpose.
        for code in _COUNTRY_ISO_CODES:
            if re.search(rf"\b{code}\b", location_text):
                return code
        normalized = normalize_text_for_fingerprint(location_text)
        for country_code, aliases in _COUNTRY_WORD_ALIASES.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", normalized):
                    return country_code
        return None

    def _extract_city(self, location_text: str, *, postal_code: str | None) -> str | None:
        cleaned = location_text
        if postal_code:
            cleaned = cleaned.replace(postal_code, " ")

        # Strip uppercase ISO codes (case-sensitive) so "Berlin, DE" → "Berlin",
        # while leaving lowercase prepositions like "at" untouched.
        for code in _COUNTRY_ISO_CODES:
            cleaned = re.sub(rf"\b{code}\b", " ", cleaned)
        for aliases in _COUNTRY_WORD_ALIASES.values():
            for alias in aliases:
                cleaned = re.sub(rf"\b{re.escape(alias)}\b", " ", cleaned, flags=re.IGNORECASE)

        segments = [normalize_whitespace(segment) for segment in re.split(r"[,;/|]", cleaned)]
        for segment in segments:
            if not segment:
                continue
            normalized_segment = normalize_whitespace(_CITY_PREFIX_RE.sub("", segment))
            if normalized_segment:
                return normalized_segment
        return None
