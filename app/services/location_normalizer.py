from __future__ import annotations

import re

from app.services.hashers import fingerprint_tokens, normalize_text_for_fingerprint, normalize_whitespace
from app.services.normalization_models import NormalizedLocation

_POSTAL_CODE_RE = re.compile(r"\b\d{5}\b")
_CITY_PREFIX_RE = re.compile(r"^(?:raum|region|nahe|naehe|bei)\s+", re.IGNORECASE)
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "DE": ("deutschland", "germany", "de"),
    "AT": ("oesterreich", "osterreich", "austria", "at"),
    "CH": ("schweiz", "switzerland", "ch"),
    "PL": ("polen", "poland", "pl"),
    "NL": ("niederlande", "netherlands", "nl"),
}


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
        normalized = normalize_text_for_fingerprint(location_text)
        for country_code, aliases in _COUNTRY_ALIASES.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", normalized):
                    return country_code
        return None

    def _extract_city(self, location_text: str, *, postal_code: str | None) -> str | None:
        cleaned = location_text
        if postal_code:
            cleaned = cleaned.replace(postal_code, " ")

        for aliases in _COUNTRY_ALIASES.values():
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
