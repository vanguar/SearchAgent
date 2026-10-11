"""Объявление, которое почти наверняка закрыто: старше года.

Скорер понижает старые объявления (vacancy_quality_signals), но понижение не
убирало их из выдачи, а агрегаторы закрытые объявления не снимают: Getir ушёл из
Германии в 2024 году, а его «E-Moped Kurier» всё ещё приходил из Adzuna. Здесь —
жёсткий отсев двух случаев:

* дата публикации старше года;
* в тексте указана дата начала работы, которая прошла больше года назад
  («Beginn zum 01.10.2022 oder früher»): площадка переподняла старое объявление
  со свежей датой, и по дате публикации его не поймать.
"""
from __future__ import annotations

import re
from datetime import date

from app.services.search_models import RuleHit

STALE_POSTING_DAYS = 365

# Дата начала работы рядом со словом, которое её так и называет.
_START_DATE_RE = re.compile(
    r"\b(?:ab|zum|beginn\w*|start\w*|eintritt\w*|einstellung\w*|arbeitsbeginn)\b[^.;\n]{0,40}?"
    r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d{2})\b",
    re.IGNORECASE,
)


def stale_posting_hit(*, posted_date: date | None, raw_text: str, today: date) -> RuleHit | None:
    """Причина скрыть объявление как устаревшее; None — устаревшим не выглядит."""
    if posted_date is not None and (today - posted_date).days > STALE_POSTING_DAYS:
        return RuleHit(code="stale_posting", label_ru="объявлению больше года — скорее всего вакансия уже закрыта")
    start_dates = [_parse(match) for match in _START_DATE_RE.finditer(raw_text or "")]
    known = [value for value in start_dates if value is not None]
    if known and all((today - value).days > STALE_POSTING_DAYS for value in known):
        latest = max(known)
        return RuleHit(
            code="stale_start_date",
            label_ru=f"начало работы указано {latest:%d.%m.%Y} — объявление устарело",
        )
    return None


def _parse(match: re.Match[str]) -> date | None:
    try:
        return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None
