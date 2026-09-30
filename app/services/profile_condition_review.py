"""Explicit transport/start constraints that need clarification, not rejection."""
from __future__ import annotations

import re
from datetime import date

from app.services.search_models import RuleHit, SearchProfileContext
from app.services.signal_negation import is_negated_signal, normalize_signal_text

_OWN_CAR = re.compile(
    r"\b(?:eigen\w*\s+(?:pkw|auto|fahrzeug)|own\s+(?:car|vehicle))\b"
)
_REQUIRED = re.compile(r"\b(?:erforderlich|notwendig|voraussetzung|zwingend|required|mandatory)\b")
_DATE = re.compile(r"\b(?:(\d{4})-(\d{2})-(\d{2})|(\d{2})\.(\d{2})\.(\d{4}))\b")


def condition_review_hits(
    text: str, profile: SearchProfileContext, *, immediate_start: bool, today: date | None = None,
) -> tuple[RuleHit, ...]:
    hits: list[RuleHit] = []
    if profile.car_available is False:
        for clause in normalize_signal_text(text).split(";"):
            if _REQUIRED.search(clause) and any(
                not is_negated_signal(clause, match) for match in _OWN_CAR.finditer(clause)
            ):
                hits.append(RuleHit(code="own_car_review", label_ru="требуется свой автомобиль; в профиле его нет"))
                break
    availability = profile.start_availability_text or ""
    match = _DATE.search(availability)
    if immediate_start and match:
        values = match.groups()
        try:
            start = date(int(values[0] or values[5]), int(values[1] or values[4]), int(values[2] or values[3]))
        except ValueError:
            start = None
        if start and start > (today or date.today()):
            hits.append(RuleHit(
                code="start_date_review",
                label_ru=f"предлагают немедленный выход; вы готовы с {start.strftime('%d.%m.%Y')}",
            ))
    return tuple(hits)
