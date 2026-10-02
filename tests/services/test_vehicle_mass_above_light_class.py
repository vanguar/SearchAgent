"""Масса выше 3,5 т — уже не категория B.

"Paketzusteller/innen bis 7,5t" поднимался в выдачу профиля с правами B:
масса 7,5 т засчитывалась только рядом со словом "Fahrer", а у курьерского
заголовка такого слова нет. Формы "7,49 t" и "7.5 Tonner" не находились вовсе.
"""
import pytest
from app.services.vehicle_class_signal_extractor import extract_vehicle_class_signals


@pytest.mark.parametrize(
    ("text", "label"),
    (
        ("Paketzusteller/innen bis 7,5t gesucht", "7,5 t"),
        ("Auslieferungsfahrer bis 7,5 t", "7,5 t"),
        ("Fahrer 7,5t", "7,5 t"),
        ("Fahrer bis 7,49 t", "7,49 t"),
        ("Fahrer bis 7.49 t", "7,49 t"),
        ("Fahrer 7,5 Tonner", "7,5 t"),
        ("Fahrer 7.5 Tonner", "7,5 t"),
    ),
)
def test_mass_above_light_class_is_heavy_evidence(text: str, label: str) -> None:
    assert label in extract_vehicle_class_signals(text).heavy_vehicle, text


@pytest.mark.parametrize("text", ("Fahrer bis 3,5 t", "Paketzusteller bis 3,5t", "Fahrer 3,5-Tonner"))
def test_light_class_mass_stays_light(text: str) -> None:
    result = extract_vehicle_class_signals(text)

    assert not result.heavy_vehicle, text
    assert result.light_commercial, text


def test_mass_without_any_vehicle_or_driver_word_is_still_ignored() -> None:
    result = extract_vehicle_class_signals("Lagerarbeiter: wir bewegen täglich bis zu 7,5 t Waren.")

    assert not result.heavy_vehicle
