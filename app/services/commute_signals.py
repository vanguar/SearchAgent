"""Дом по расписанию переезда и риск «не успеть к началу смены».

Дом. Человек переезжает: с 01.11.2026 он живёт в Neustrelitz. Дорога до работы
должна считаться от того дома, который действует на сегодня, — расписание
задаётся в конфиге (HOME_CITY_SCHEDULE="2026-11-01=Neustrelitz"). Пока ни одна
запись не вступила в силу, действует город из профиля.

Смена. Если до работы больше часа (оценка по расстоянию и средней скорости из
конфига), а смена начинается до 06:00 или ночью, без переезда к началу не
успеть: общественный транспорт в это время почти не ходит. Это риск на
карточке, а не отказ.
"""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.signal_negation import is_negated_signal, normalize_signal_text

_SCHEDULE_ENTRY_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*=\s*(.+?)\s*$")


def parse_home_schedule(raw: str | None) -> tuple[tuple[date, str], ...]:
    """«2026-11-01=Neustrelitz; 2027-03-01=Rostock» → отсортированные пары."""
    entries: list[tuple[date, str]] = []
    for part in re.split(r"[;\n]", raw or ""):
        match = _SCHEDULE_ENTRY_RE.match(part)
        if not match:
            continue
        try:
            effective = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        entries.append((effective, match.group(2)))
    return tuple(sorted(entries))


def effective_home_city(
    profile_city: str | None,
    *,
    today: date,
    config: RelevanceConfig | None = None,
) -> str | None:
    """Город дома на сегодня: последняя вступившая в силу запись расписания или город профиля."""
    schedule = parse_home_schedule((config or get_relevance_config()).home_city_schedule)
    current = profile_city
    for effective, city in schedule:
        if effective <= today:
            current = city
    return current


@lru_cache(maxsize=4)
def _early_shift_patterns(config: RelevanceConfig) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in config.early_shift_patterns)


def has_early_shift(text: str | None, *, config: RelevanceConfig | None = None) -> bool:
    """Смена начинается до 06:00 или ночью (Nachtschicht, ab 2 Uhr, frühmorgens)."""
    normalized = normalize_signal_text(text)
    for pattern in _early_shift_patterns(config or get_relevance_config()):
        for match in pattern.finditer(normalized):
            if not is_negated_signal(normalized, match):
                return True
    return False


def commute_minutes(distance_km: float | None, *, config: RelevanceConfig | None = None) -> int | None:
    if distance_km is None:
        return None
    speed = max(1.0, (config or get_relevance_config()).commute_average_speed_kmh)
    return round(distance_km / speed * 60)
