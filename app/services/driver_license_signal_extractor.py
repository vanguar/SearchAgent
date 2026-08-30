from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

DriverLicenseCategory = str

_CATEGORY_ORDER = (
    "AM",
    "A1",
    "A2",
    "A",
    "B",
    "B96",
    "B196",
    "BE",
    "C1",
    "C1E",
    "C",
    "CE",
    "D1",
    "D1E",
    "D",
    "DE",
    "L",
    "T",
    "LKW",
)
_CATEGORY_PATTERN = r"b196|b96|c1e|d1e|c1|d1|a1|a2|am|be|ce|de|a|b|c|d|l|t"
_CATEGORY_RE = re.compile(rf"\b({_CATEGORY_PATTERN})\b")
_GENDER_MARKER_RE = re.compile(r"\(?\b[mdwf]\s*/\s*[mdwf]\s*/\s*[mdwf]\b\)?", re.IGNORECASE)
_ABBREVIATION_RE = re.compile(r"\b(kl|kat)\.", re.IGNORECASE)
_CLAUSE_SEPARATOR_RE = re.compile(r"[,;:.\n]+")
_NON_SIGNAL_CHARACTER_RE = re.compile(r"[^0-9a-z/+.,;:\s-]+")
_SPACES_RE = re.compile(r"\s+")

_CATEGORY_CONNECTOR_PATTERN = r"/|\+|,|oder|und|or|and"
_CATEGORY_QUALIFIER_PATTERN = r"idealerweise|vorzugsweise|preferably|optionally"
_CATEGORY_SEQUENCE = (
    rf"(?P<categories>(?:{_CATEGORY_PATTERN})"
    rf"(?:\s*(?:{_CATEGORY_CONNECTOR_PATTERN})\s*"
    rf"(?:(?:{_CATEGORY_QUALIFIER_PATTERN})\s+)?(?:{_CATEGORY_PATTERN}))*)"
)
_LICENSE_ANCHOR_PATTERN = (
    r"fuhrerschein(?:klasse)?|fahrerlaubnis(?:klasse)?|"
    r"(?:driving|driver(?:\s+s)?|hgv|lgv|truck)\s+licen[cs]e|licen[cs]e"
)
_LICENSE_CLASS_PREFIX_PATTERN = r"(?:der\s+)?(?:klasse|kategorie|kat|class|category)"
_LICENSE_BEFORE_CATEGORY_RE = re.compile(
    rf"\b(?:(?:lkw|pkw)\s+)?(?:{_LICENSE_ANCHOR_PATTERN})"
    rf"(?:\s+{_LICENSE_CLASS_PREFIX_PATTERN})?\s+{_CATEGORY_SEQUENCE}\b"
)
_CLASS_BEFORE_CATEGORY_RE = re.compile(
    rf"\b(?:fuhrerscheinklasse|fahrerlaubnisklasse|fs\s+(?:klasse|kl)|klasse|kl|kategorie|kat)"
    rf"\s+{_CATEGORY_SEQUENCE}\b"
)
_CATEGORY_BEFORE_LICENSE_RE = re.compile(
    rf"\b{_CATEGORY_SEQUENCE}\s+(?:(?:lkw|pkw)\s+)?(?:{_LICENSE_ANCHOR_PATTERN})\b"
)
_CATEGORY_PAIR_RE = re.compile(
    rf"\b(?P<categories>(?:{_CATEGORY_PATTERN})\s*(?:/|\+)\s*(?:{_CATEGORY_PATTERN}))\b"
)
_BARE_MARKED_CATEGORY_RE = re.compile(
    rf"\b(?P<categories>{_CATEGORY_PATTERN})\b\s+(?:zwingend\s+)?"
    r"(?:erforderlich|notwendig|vorausgesetzt|pflicht|optional|wunschenswert|erwunscht|bevorzugt|"
    r"von\s+vorteil|ware\s+(?:ideal|ein\s+plus)|nicht\s+(?:erforderlich|notwendig)|kann\s+(?:spater\s+)?"
    r"(?:erworben|nachgeholt)\s+werden|vorhanden|required|mandatory|essential|preferred|desirable|"
    r"an\s+advantage|nice\s+to\s+have|not\s+required|not\s+necessary)\b"
)
_LANGUAGE_CONTEXT_RE = re.compile(
    r"\b(?:deutsch|deutschkenntnisse|german|sprache|language|sprachniveau|sprachlevel|niveau|level)\s*$"
)
_NON_LICENSE_CONTEXT_RE = re.compile(r"\b(?:vitamin|produkt|product|software|zertifizierung|certification)\s*$")
_HEAVY_VEHICLE_LICENSE_RE = re.compile(
    r"\b(?:lkw\s+fuhrerschein|(?:hgv|lgv)\s+licen[cs]e)\b"
)

_B_ALTERNATIVE_RE = re.compile(
    rf"\bb\s*(?:/|oder|or)\s*(?:{_CATEGORY_PATTERN})\b|"
    rf"\b(?:{_CATEGORY_PATTERN})\s*(?:/|oder|or)\s*b\b"
)
_OPTIONAL_RE = re.compile(
    r"\b(?:nicht\s+(?:erforderlich|notwendig)|optional|wunschenswert|erwunscht|bevorzugt|"
    r"von\s+vorteil|ware\s+ideal|kann\s+(?:erworben|nachgeholt)\s+werden|"
    r"not\s+(?:required|necessary)|preferred|desirable|an\s+advantage|nice\s+to\s+have|would\s+be\s+ideal)\b"
)
_MANDATORY_RE = re.compile(
    r"\b(?:erforderlich|notwendig|vorausgesetzt|voraussetzung|pflicht|benotigt|notig|muss|"
    r"required|mandatory|essential|needed|must\s+have)\b"
)
_B_SUFFICIENT_RE = re.compile(
    r"\b(?:mindestens\s+(?:klasse\s+)?b|(?:klasse\s+)?b\s+(?:ausreichend|genugt|reicht\s+aus)|"
    r"(?:at\s+least|minimum)\s+(?:class\s+)?b|(?:class\s+)?b\s+(?:is\s+)?(?:sufficient|enough))\b"
)


@dataclass(frozen=True, slots=True)
class DriverLicenseRequirement:
    required: tuple[DriverLicenseCategory, ...] = ()
    allowed: tuple[DriverLicenseCategory, ...] = ()
    optional: tuple[DriverLicenseCategory, ...] = ()
    mentioned: tuple[DriverLicenseCategory, ...] = ()


def extract_driver_license_requirements(text: str | None) -> DriverLicenseRequirement:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return DriverLicenseRequirement()

    required: set[str] = set()
    allowed: set[str] = set()
    optional: set[str] = set()
    mentioned = _extract_mentioned_categories(normalized)

    for clause in _CLAUSE_SEPARATOR_RE.split(normalized):
        for segment in _split_mixed_requirement_clause(clause):
            categories, has_explicit_license_context = _extract_clause_categories(segment)
            if not categories:
                continue
            mentioned.update(categories)

            if _B_ALTERNATIVE_RE.search(segment):
                allowed.update(categories)
                continue

            if _OPTIONAL_RE.search(segment):
                optional.update(categories)
                continue

            if "b" in categories and _B_SUFFICIENT_RE.search(segment):
                allowed.add("b")
                categories.discard("b")
                if not categories:
                    continue

            if has_explicit_license_context or _MANDATORY_RE.search(segment):
                required.update(categories)

    return DriverLicenseRequirement(
        required=_ordered_categories(required),
        allowed=_ordered_categories(allowed),
        optional=_ordered_categories(optional),
        mentioned=_ordered_categories(mentioned),
    )


def extract_profile_driver_license_categories(value: str | None) -> tuple[DriverLicenseCategory, ...]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ()
    return _ordered_categories(set(_CATEGORY_RE.findall(normalized)))


def _extract_clause_categories(clause: str) -> tuple[set[str], bool]:
    categories: set[str] = set()
    has_explicit_license_context = False

    for pattern in (
        _LICENSE_BEFORE_CATEGORY_RE,
        _CLASS_BEFORE_CATEGORY_RE,
        _CATEGORY_BEFORE_LICENSE_RE,
    ):
        for match in pattern.finditer(clause):
            categories.update(_CATEGORY_RE.findall(match.group("categories")))
            has_explicit_license_context = True

    for match in _CATEGORY_PAIR_RE.finditer(clause):
        categories.update(_CATEGORY_RE.findall(match.group("categories")))
        has_explicit_license_context = True

    for match in _BARE_MARKED_CATEGORY_RE.finditer(clause):
        preceding_text = clause[: match.start()]
        if _LANGUAGE_CONTEXT_RE.search(preceding_text) or _NON_LICENSE_CONTEXT_RE.search(preceding_text):
            continue
        categories.update(_CATEGORY_RE.findall(match.group("categories")))

    return categories, has_explicit_license_context


def _extract_mentioned_categories(text: str) -> set[str]:
    categories: set[str] = set()
    for pattern in (
        _LICENSE_BEFORE_CATEGORY_RE,
        _CLASS_BEFORE_CATEGORY_RE,
        _CATEGORY_BEFORE_LICENSE_RE,
        _CATEGORY_PAIR_RE,
    ):
        for match in pattern.finditer(text):
            categories.update(_CATEGORY_RE.findall(match.group("categories")))

    for match in _BARE_MARKED_CATEGORY_RE.finditer(text):
        preceding_text = text[: match.start()]
        if _LANGUAGE_CONTEXT_RE.search(preceding_text) or _NON_LICENSE_CONTEXT_RE.search(preceding_text):
            continue
        categories.update(_CATEGORY_RE.findall(match.group("categories")))

    if _HEAVY_VEHICLE_LICENSE_RE.search(text) and not any(category != "b" for category in categories):
        categories.add("lkw")
    return categories


def _split_mixed_requirement_clause(clause: str) -> tuple[str, ...]:
    clause = clause.strip()
    if not clause:
        return ()
    if not (_OPTIONAL_RE.search(clause) and _MANDATORY_RE.search(clause)):
        return (clause,)
    return tuple(segment.strip() for segment in re.split(r"\b(?:und|and|aber|but)\b", clause) if segment.strip())


def _normalize_requirement_text(text: str | None) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    folded = _GENDER_MARKER_RE.sub(" ", folded.casefold())
    folded = _ABBREVIATION_RE.sub(lambda match: f"{match.group(1)} ", folded)
    folded = folded.replace("-", " ")
    folded = _NON_SIGNAL_CHARACTER_RE.sub(" ", folded)
    return _SPACES_RE.sub(" ", folded).strip()


def _ordered_categories(categories: set[str]) -> tuple[str, ...]:
    return tuple(category for category in _CATEGORY_ORDER if category.casefold() in categories)
