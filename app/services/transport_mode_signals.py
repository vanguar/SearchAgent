"""На чём делается работа: автомобиль, велосипед или пешком.

Курьерская вакансия на e-Bike и курьерская вакансия на Sprinter называются
одинаково — «Kurierfahrer». Для профиля, который работает только на машине,
первая не подходит, сколько бы совпадений с ролью у неё ни было.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.hashers import normalize_text_for_fingerprint
from app.services.signal_negation import is_negated_signal, normalize_signal_text

TRANSPORT_MODE_LABELS_RU: dict[str, str] = {
    "car": "легковой автомобиль",
    "van": "фургон",
    "bike": "велосипед / e-Bike",
    "foot": "пешком",
}


@dataclass(frozen=True, slots=True)
class TransportModeSignals:
    # Способы, названные в заголовке и в описании (кроме машины).
    title_modes: tuple[str, ...] = ()
    body_modes: tuple[str, ...] = ()
    # Машина названа в заголовке / где-либо в тексте (не в отрицании).
    car_in_title: bool = False
    car_mentioned: bool = False
    # Разноска газет и рекламы — нецелевая роль для профиля на машине.
    press_distribution: bool = False


@lru_cache(maxsize=4)
def _compiled(config: RelevanceConfig) -> dict[str, tuple[tuple[str, re.Pattern[str]], ...]]:
    return {
        "title": tuple((mode, re.compile(p)) for mode, p in config.transport_mode_title_patterns),
        "body": tuple((mode, re.compile(p)) for mode, p in config.transport_mode_body_patterns),
        "car": tuple(("car", re.compile(p)) for p in config.car_mode_patterns),
        "press": tuple(("press", re.compile(p)) for p in config.press_distribution_patterns),
    }


def extract_transport_mode_signals(
    *,
    title: str | None,
    text: str | None,
    config: RelevanceConfig | None = None,
) -> TransportModeSignals:
    patterns = _compiled(config or get_relevance_config())
    title_text = normalize_text_for_fingerprint(title)
    body = normalize_signal_text(text)

    title_modes = _modes(title_text, patterns["title"], negation_aware=False)
    body_modes = _modes(body, patterns["body"], negation_aware=True)
    car_in_title = bool(_modes(title_text, patterns["car"], negation_aware=False))
    car_mentioned = car_in_title or bool(_modes(body, patterns["car"], negation_aware=True))
    press = bool(_modes(title_text, patterns["press"], negation_aware=False)) or bool(
        _modes(body, patterns["press"], negation_aware=True)
    )
    return TransportModeSignals(
        title_modes=title_modes,
        body_modes=body_modes,
        car_in_title=car_in_title,
        car_mentioned=car_mentioned,
        press_distribution=press,
    )


def unsupported_transport_modes(signals: TransportModeSignals, profile_modes: tuple[str, ...]) -> tuple[str, ...]:
    """Способы, которыми делается работа и которых нет в профиле.

    Заголовок решает сам, если в нём не названа и машина. Описание решает только
    когда машина не упомянута нигде: «per Fahrrad oder mit dem Auto» — это
    выбор, а не условие.
    """
    if not profile_modes:
        return ()
    missing: list[str] = []
    if not signals.car_in_title:
        missing.extend(mode for mode in signals.title_modes if mode not in profile_modes)
    if not signals.car_mentioned:
        missing.extend(mode for mode in signals.body_modes if mode not in profile_modes)
    return tuple(dict.fromkeys(missing))


def _modes(
    text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    negation_aware: bool,
) -> tuple[str, ...]:
    found: list[str] = []
    for mode, pattern in patterns:
        if mode in found:
            continue
        for match in pattern.finditer(text):
            if negation_aware and is_negated_signal(text, match):
                continue
            found.append(mode)
            break
    return tuple(found)
