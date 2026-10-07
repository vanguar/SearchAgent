"""Требования к правам и допускам, которых нет в общем извлекателе категорий.

Общий извлекатель (driver_license_signal_extractor) ищет категорию рядом со
словами «Führerschein», «Klasse» и т.п. Здесь — то, что он не видит:

* категория, названная в заголовке голым кодом: «Kraftfahrer CE – Auslieferung»,
  «Fahrer Paketzustellung C1 Pakete»;
* P-Schein (Fahrerlaubnis zur Fahrgastbeförderung) — перевозка людей;
* квалифицированная перевозка больных, где нужен Rettungssanitäter;
* признаки риска, которые не повод скрывать: Fernverkehr, погрузчик, Ladekran.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.hashers import normalize_text_for_fingerprint
from app.services.signal_negation import is_negated_signal, normalize_signal_text

# Голый код категории в заголовке. «C» без цифры и пары не берётся: слишком много
# ложных совпадений, а «C/CE» и «Klasse C» ловит общий извлекатель.
_TITLE_CLASS_RE = re.compile(r"\b(c1e|c1|ce)\b")
# Слова, после которых «C1» — уровень языка, а не категория прав.
_LANGUAGE_BEFORE_RE = re.compile(r"\b(?:deutsch\w*|englisch\w*|english|german|sprach\w*|niveau|level)\s+(?:\w+\s+)?$")
# Заголовок про вождение или перевозку: только в нём голый код читается как права.
_DRIVING_TITLE_RE = re.compile(
    r"fahrer|kurier|zustell|paket|liefer|transport|lkw|spedition|ausliefer|logistik|tour"
)
_OPTIONAL_NEARBY_RE = re.compile(
    r"\b(?:von\s+vorteil|wunschenswert|erwunscht|bevorzugt|optional|ideal|idealerweise|falls\s+erforderlich|"
    r"wenn\s+vorhanden|nice\s+to\s+have|kein\s+muss|"
    r"kann\s+(?:spater\s+)?(?:erworben|gemacht|nachgeholt)\s+werden|wird\s+(?:gestellt|finanziert|ermoglicht|bezahlt)|"
    r"unterstutzen\s+(?:dich|sie)\s+bei)\b"
)
_WITHOUT_RE = re.compile(r"\b(?:auch\s+)?ohne\s+(?:\w+\s+){0,2}$|\bkein\w*\s+(?:\w+\s+){0,2}$")


@dataclass(frozen=True, slots=True)
class LicenseRequirementSignals:
    # Категории, названные кодом в заголовке («CE», «C1»).
    title_categories: tuple[str, ...] = ()
    # P-Schein обязателен: назван как требование или вытекает из заголовка.
    requires_p_schein: bool = False
    # P-Schein упомянут как желательный — риск, а не повод скрыть.
    p_schein_optional: bool = False
    # Перевозка больных с медицинским допуском.
    medical_transport_required: bool = False
    # Риски: (код, подпись).
    risk_markers: tuple[tuple[str, str], ...] = ()


@lru_cache(maxsize=4)
def _compiled(config: RelevanceConfig) -> dict[str, tuple[re.Pattern[str], ...]]:
    return {
        "p_req": tuple(re.compile(p) for p in config.p_schein_requirement_patterns),
        "p_title": tuple(re.compile(p) for p in config.p_schein_title_patterns),
        "med_title": tuple(re.compile(p) for p in config.medical_transport_title_patterns),
        "med_req": tuple(re.compile(p) for p in config.medical_transport_requirement_patterns),
    }


def extract_license_requirement_signals(
    *,
    title: str | None,
    text: str | None,
    config: RelevanceConfig | None = None,
) -> LicenseRequirementSignals:
    resolved = config or get_relevance_config()
    patterns = _compiled(resolved)
    title_text = normalize_text_for_fingerprint(title)
    body = normalize_signal_text(text)

    title_categories = _title_categories(title_text)

    p_required = False
    p_optional = False
    if any(pattern.search(title_text) for pattern in patterns["p_title"]):
        p_required = True
    for pattern in patterns["p_req"]:
        for match in pattern.finditer(body):
            if _is_waived(body, match):
                continue
            if _is_optional(body, match):
                p_optional = True
            else:
                p_required = True
    # «Fahrer (m/w/d) mit P-Schein gesucht» — заголовок прямо называет допуск.
    if any(pattern.search(title_text) for pattern in patterns["p_req"]):
        title_match = next(m for p in patterns["p_req"] if (m := p.search(title_text)))
        if not _WITHOUT_RE.search(title_text[: title_match.start()]):
            p_required = True

    medical = any(pattern.search(title_text) for pattern in patterns["med_title"])
    if not medical:
        for pattern in patterns["med_req"]:
            if any(not _is_waived(body, match) for match in pattern.finditer(body)):
                medical = True
                break

    risks: list[tuple[str, str]] = []
    for code, pattern_text, label in resolved.license_risk_markers:
        pattern = re.compile(pattern_text)
        if any(not is_negated_signal(body, match) for match in pattern.finditer(body)):
            risks.append((code, label))

    return LicenseRequirementSignals(
        title_categories=title_categories,
        requires_p_schein=p_required,
        p_schein_optional=p_optional and not p_required,
        medical_transport_required=medical,
        risk_markers=tuple(risks),
    )


def _title_categories(title_text: str) -> tuple[str, ...]:
    if not title_text or not _DRIVING_TITLE_RE.search(title_text):
        return ()
    found: list[str] = []
    for match in _TITLE_CLASS_RE.finditer(title_text):
        if _LANGUAGE_BEFORE_RE.search(title_text[: match.start()]):
            continue
        if _WITHOUT_RE.search(title_text[: match.start()]):
            continue
        category = match.group(1).upper()
        if category not in found:
            found.append(category)
    return tuple(found)


def _is_waived(text: str, match: re.Match[str]) -> bool:
    """«auch ohne P-Schein», «kein P-Schein nötig», «P-Schein nicht erforderlich»."""
    if is_negated_signal(text, match):
        return True
    prefix = text[max(0, match.start() - 40): match.start()]
    return bool(_WITHOUT_RE.search(prefix))


def _is_optional(text: str, match: re.Match[str]) -> bool:
    end = text.find(";", match.end())
    window = text[match.end(): end if end >= 0 else len(text)][:80]
    start = text.rfind(";", 0, match.start()) + 1
    before = text[max(start, match.start() - 60): match.start()]
    return bool(_OPTIONAL_NEARBY_RE.search(window) or _OPTIONAL_NEARBY_RE.search(before))


# Какие категории открывает каждая: CE включает C, C1, C1E, BE и B, и т.д.
_IMPLIED_CLASSES: dict[str, frozenset[str]] = {
    "CE": frozenset({"C", "C1", "C1E", "BE", "B"}),
    "C": frozenset({"C1", "B"}),
    "C1E": frozenset({"C1", "BE", "B"}),
    "C1": frozenset({"B"}),
    "DE": frozenset({"D", "D1", "D1E", "BE", "B"}),
    "D": frozenset({"D1", "B"}),
    "D1E": frozenset({"D1", "BE", "B"}),
    "D1": frozenset({"B"}),
    "BE": frozenset({"B"}),
    "B96": frozenset({"B"}),
    "B196": frozenset({"B"}),
}
_TRUCK_CLASSES = frozenset({"C1", "C1E", "C", "CE"})


def covered_license_classes(classes: tuple[str, ...]) -> frozenset[str]:
    """Все категории, которые закрывают права профиля, включая подразумеваемые.

    «LKW» в тексте объявления — это грузовая категория без уточнения; её
    закрывает любая из C-категорий.
    """
    covered: set[str] = set(classes)
    for code in classes:
        covered |= _IMPLIED_CLASSES.get(code, frozenset())
    if covered & _TRUCK_CLASSES:
        covered.add("LKW")
    return frozenset(covered)


def has_truck_class(classes: tuple[str, ...]) -> bool:
    return bool(covered_license_classes(classes) & _TRUCK_CLASSES)
