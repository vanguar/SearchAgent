"""Opt-in search policies; never change the person's shared language settings."""
from typing import Literal

StrictMatchMode = Literal["off", "no_german", "a1"]

STRICT_MATCH_CHOICES: tuple[tuple[str, str], ...] = (
    ("off", "Обычный отбор"),
    ("no_german", "Строгий: без немецкого"),
    ("a1", "Строгий: без немецкого или до A1"),
)


def validate_strict_match_mode(value: object) -> StrictMatchMode:
    if value == "off":
        return "off"
    if value == "no_german":
        return "no_german"
    if value == "a1":
        return "a1"
    raise ValueError("Выберите режим отбора из списка.")
