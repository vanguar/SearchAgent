"""Требования к допуску к работе: что именно требуют и закрывает ли это профиль.

Раньше любое слово «Arbeitserlaubnis» давало риск «есть вопросы по допуску к
работе». У Deutsche Post оно стоит в условии мини-джоба: «Keine Einschränkungen
innerhalb der Arbeitserlaubnis. Beschäftigung nur möglich mit mehr als 20
Stunden/Woche» — это не требование, а у человека с правом на работу (§ 24) и
настоящее «Arbeitserlaubnis erforderlich» риском не является.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.signal_negation import is_negated_signal, normalize_signal_text

_INFORMATIONAL_WINDOW = 60


@dataclass(frozen=True, slots=True)
class WorkAuthorizationRequirement:
    kind: str
    label_ru: str
    satisfied_by_work_permit: bool


@lru_cache(maxsize=4)
def _compiled(
    config: RelevanceConfig,
) -> tuple[tuple[tuple[WorkAuthorizationRequirement, re.Pattern[str]], ...], tuple[re.Pattern[str], ...]]:
    requirements = tuple(
        (WorkAuthorizationRequirement(kind=kind, label_ru=label, satisfied_by_work_permit=satisfied), re.compile(pattern))
        for kind, pattern, label, satisfied in config.work_authorization_requirements
    )
    informational = tuple(re.compile(pattern) for pattern in config.work_authorization_informational_patterns)
    return requirements, informational


def extract_work_authorization_requirements(
    text: str | None,
    *,
    config: RelevanceConfig | None = None,
) -> tuple[WorkAuthorizationRequirement, ...]:
    """Требования, названные в тексте как требования (без отрицаний и пояснений)."""
    requirements, informational = _compiled(config or get_relevance_config())
    normalized = normalize_signal_text(text)
    found: list[WorkAuthorizationRequirement] = []
    for requirement, pattern in requirements:
        if any(existing.kind == requirement.kind for existing in found):
            continue
        for match in pattern.finditer(normalized):
            if is_negated_signal(normalized, match):
                continue
            window = normalized[max(0, match.start() - _INFORMATIONAL_WINDOW): match.end() + _INFORMATIONAL_WINDOW]
            if any(marker.search(window) for marker in informational):
                continue
            found.append(requirement)
            break
    return tuple(found)


def unmet_work_authorization(
    requirements: tuple[WorkAuthorizationRequirement, ...],
    *,
    work_authorized: bool,
) -> tuple[WorkAuthorizationRequirement, ...]:
    """Требования, которые профиль не закрывает."""
    return tuple(
        requirement for requirement in requirements
        if not (work_authorized and requirement.satisfied_by_work_permit)
    )
