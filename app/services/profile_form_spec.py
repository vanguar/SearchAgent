"""Разбор формы поискового профиля в набор полей.

Зачем отдельный модуль. Форма создания и форма редактирования задают ОДНИ И ТЕ ЖЕ
критерии, и разбор у них обязан быть один: иначе поле, добавленное в одну форму,
молча не работает в другой.

Главное правило разбора: «не указано» — это значащее состояние, а не повод
подставить значение по умолчанию. Пустое поле даёт None, и профиль сохраняет
None; правила поиска умеют отличать «человек сказал нет» от «человек не сказал
ничего», и подменять одно другим нельзя.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.services.employment_signal_extractor import EMPLOYMENT_TYPE_LABELS_RU, EMPLOYMENT_TYPE_ORDER

# Разделители, которыми человек перечисляет несколько значений в одном поле.
_LIST_SPLIT_RE = re.compile(r"[,;\n]+")

# Категории водительских прав, которые предлагает форма. Пустое значение —
# «не указано»; оно НЕ равно «прав нет».
# «P» — P-Schein (Fahrerlaubnis zur Fahrgastbeförderung): без него вакансии
# на перевозку пассажиров скрываются.
DRIVER_LICENSE_OPTIONS: tuple[str, ...] = (
    "AM", "A1", "A2", "A", "B", "BE", "B96", "B196", "C1", "C1E", "C", "CE", "D1", "D1E", "D", "DE", "L", "T", "P",
)

# Способы передвижения на работе, в фиксированном порядке.
TRANSPORT_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("car", "Легковой автомобиль"),
    ("van", "Фургон / Transporter"),
    ("bike", "Велосипед / e-Bike"),
    ("foot", "Пешком"),
)
TRANSPORT_MODE_ORDER: tuple[str, ...] = tuple(code for code, _ in TRANSPORT_MODE_CHOICES)

EMPLOYMENT_TYPE_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (code, EMPLOYMENT_TYPE_LABELS_RU[code]) for code in EMPLOYMENT_TYPE_ORDER
)

# Верхняя граница радиуса. Дальше речь уже не о дороге на работу, а о переезде,
# и для этого есть отдельное поле «готовность к переезду».
MAX_PROFILE_RADIUS_KM = 200
# Варианты радиуса, которые предлагают И форма профиля, И форма поиска. Один
# список на оба места: иначе значения в них разъезжаются. Ноль оставлен как
# осмысленный выбор — «строго в перечисленных городах».
SEARCH_RADIUS_OPTIONS: tuple[int, ...] = (0, 10, 25, 50, 75, 100, 150, 200)
# Потолок ориентира по оплате: выше этого человек почти наверняка ошибся полем.
MAX_HOURLY_RATE_EUR = 1000.0


@dataclass(frozen=True, slots=True)
class ProfileFormFields:
    """Критерии профиля, разобранные из формы.

    Списковые поля хранятся кортежами в порядке из формы: первый элемент —
    основной, остальные — дополнительные. Порядок и есть приоритет.
    """

    name: str | None = None
    desired_roles: tuple[str, ...] = ()
    excluded_roles: tuple[str, ...] = ()
    search_query_terms: tuple[str, ...] = ()
    additional_search_terms: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()
    search_radius_km: int | None = None
    employment_types: tuple[str, ...] = ()
    transport_modes: tuple[str, ...] = ()
    min_salary_eur_per_hour: float | None = None
    physical_work_ok: bool | None = None
    driver_license: str | None = None
    car_available: bool | None = None
    self_employment_ok: bool | None = None
    relocation_ready: bool | None = None
    shift_ok: bool | None = None
    housing_needed: bool | None = None
    start_availability_text: str | None = None
    no_german_required: bool = False
    notes: str | None = None
    # Поля, реально присутствовавшие в форме. Нужны обновлению: отсутствующее поле
    # не трогается, а присутствующее и пустое — очищается. Без этого различия
    # нельзя ни очистить список, ни безопасно принять частичную форму.
    present_fields: frozenset[str] = field(default_factory=frozenset)

    def was_submitted(self, name: str) -> bool:
        return name in self.present_fields


def parse_profile_form(form_data: Mapping[str, object]) -> ProfileFormFields:
    """Разобрать форму профиля. Отсутствующие поля остаются None/пустыми."""
    present = {key for key in _FIELD_NAMES if key in form_data}
    # Чекбоксы браузер не присылает, когда они сняты, поэтому их присутствие
    # определяется по скрытому маркеру: иначе снять галочку было бы невозможно.
    for checkbox, marker in _CHECKBOX_MARKERS.items():
        if marker in form_data:
            present.add(checkbox)

    return ProfileFormFields(
        name=_text(form_data, "name"),
        desired_roles=_merged_list(form_data, "primary_role", "additional_roles"),
        excluded_roles=_list(form_data, "excluded_roles"),
        search_query_terms=_list(form_data, "search_query_terms"),
        additional_search_terms=_list(form_data, "additional_search_terms"),
        preferred_locations=_merged_list(form_data, "primary_city", "additional_cities"),
        search_radius_km=_bounded_int(form_data, "search_radius_km", 0, MAX_PROFILE_RADIUS_KM),
        employment_types=_employment_types(form_data),
        transport_modes=_transport_modes(form_data),
        min_salary_eur_per_hour=_bounded_float(form_data, "min_salary_eur_per_hour", 0.0, MAX_HOURLY_RATE_EUR),
        physical_work_ok=_tristate(form_data, "physical_work_ok"),
        driver_license=_driver_license(form_data),
        car_available=_tristate(form_data, "car_available"),
        self_employment_ok=_tristate(form_data, "self_employment_ok"),
        relocation_ready=_tristate(form_data, "relocation_ready"),
        shift_ok=_tristate(form_data, "shift_ok"),
        housing_needed=_tristate(form_data, "housing_needed"),
        start_availability_text=_text(form_data, "start_availability_text"),
        no_german_required=_checkbox(form_data, "no_german_required"),
        notes=_text(form_data, "notes"),
        present_fields=frozenset(present),
    )


# Имена полей формы, чьё присутствие отслеживается. Составные списковые поля
# отмечены обоими своими именами: достаточно одного, чтобы список считался
# заданным.
_FIELD_NAMES: frozenset[str] = frozenset({
    "name",
    "primary_role",
    "additional_roles",
    "excluded_roles",
    "search_query_terms",
    "additional_search_terms",
    "primary_city",
    "additional_cities",
    "search_radius_km",
    "employment_types",
    "transport_modes",
    "min_salary_eur_per_hour",
    "physical_work_ok",
    "driver_license",
    "car_available",
    "self_employment_ok",
    "relocation_ready",
    "shift_ok",
    "housing_needed",
    "start_availability_text",
    "no_german_required",
    "notes",
})
_CHECKBOX_MARKERS: dict[str, str] = {
    "no_german_required": "no_german_required_present",
    "employment_types": "employment_types_present",
    "transport_modes": "transport_modes_present",
}


def _text(form_data: Mapping[str, object], key: str) -> str | None:
    raw = form_data.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _list(form_data: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = _text(form_data, key)
    if raw is None:
        return ()
    seen: set[str] = set()
    values: list[str] = []
    for part in _LIST_SPLIT_RE.split(raw):
        value = " ".join(part.split())
        if not value:
            continue
        marker = value.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        values.append(value)
    return tuple(values)


def _merged_list(form_data: Mapping[str, object], primary_key: str, extra_key: str) -> tuple[str, ...]:
    """Основное значение плюс дополнительные, без повторов и в этом порядке."""
    primary = _list(form_data, primary_key)
    extra = _list(form_data, extra_key)
    seen: set[str] = set()
    merged: list[str] = []
    for value in (*primary, *extra):
        marker = value.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(value)
    return tuple(merged)


def _tristate(form_data: Mapping[str, object], key: str) -> bool | None:
    """Да / Нет / Не указано. Пустая строка — это именно «не указано»."""
    raw = _text(form_data, key)
    if raw is None:
        return None
    marker = raw.casefold()
    if marker in {"true", "yes", "да", "1", "on"}:
        return True
    if marker in {"false", "no", "нет", "0", "off"}:
        return False
    return None


def _checkbox(form_data: Mapping[str, object], key: str) -> bool:
    raw = form_data.get(key)
    if raw is None:
        return False
    return str(raw).strip().casefold() in {"1", "true", "yes", "on", "да"}


def _bounded_int(form_data: Mapping[str, object], key: str, low: int, high: int) -> int | None:
    raw = _text(form_data, key)
    if raw is None:
        return None
    try:
        value = int(float(raw.replace(",", ".")))
    except ValueError:
        return None
    return max(low, min(high, value))


def _bounded_float(form_data: Mapping[str, object], key: str, low: float, high: float) -> float | None:
    raw = _text(form_data, key)
    if raw is None:
        return None
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        # Ноль как ориентир по оплате смысла не несёт — это «не указано».
        return None
    return max(low, min(high, value))


def _driver_license(form_data: Mapping[str, object]) -> str | None:
    """Категории прав. Принимаются только известные обозначения.

    Свободный текст сюда пускать нельзя: по этому полю работает жёсткий фильтр
    по классу транспорта, и опечатка в нём меняет выдачу молча.

    Поле читается как повторяющееся, потому что открытых категорий у человека
    бывает несколько (B и BE — обычное дело), и форма отмечает их набором
    галочек с одним именем. Одно значение со списком через запятую разбирается
    так же, как раньше: это тот же набор, записанный одной строкой.
    """
    parts = [
        part.strip().upper()
        for value in _multi_values(form_data, "driver_license")
        for part in _LIST_SPLIT_RE.split(value)
        if part.strip()
    ]
    if not parts:
        return None
    candidates = set(parts)
    allowed = [option for option in DRIVER_LICENSE_OPTIONS if option in candidates]
    return ", ".join(allowed) or None


def _employment_types(form_data: Mapping[str, object]) -> tuple[str, ...]:
    """Отмеченные формы занятости, в фиксированном порядке.

    Порядок фиксированный, а не из формы: он попадает в подписи на карточке, и
    один и тот же набор должен читаться одинаково при каждом сохранении.
    """
    raw_values = _multi_values(form_data, "employment_types")
    selected = {value.strip() for value in raw_values if value.strip()}
    return tuple(code for code in EMPLOYMENT_TYPE_ORDER if code in selected)


def _transport_modes(form_data: Mapping[str, object]) -> tuple[str, ...]:
    """Отмеченные способы передвижения в фиксированном порядке; неизвестные коды отбрасываются."""
    selected = {value.strip() for value in _multi_values(form_data, "transport_modes") if value.strip()}
    return tuple(code for code in TRANSPORT_MODE_ORDER if code in selected)


def _multi_values(form_data: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Значения поля, которое может повторяться (checkbox-группа).

    Starlette отдаёт такие поля через getlist; обычный Mapping — одним значением.
    Поддерживаются оба, чтобы разбор работал и в тестах со словарём.
    """
    getlist = getattr(form_data, "getlist", None)
    if callable(getlist):
        return tuple(str(value) for value in getlist(key))
    raw = form_data.get(key)
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(value) for value in raw)
    return tuple(str(value) for value in _LIST_SPLIT_RE.split(str(raw)) if value.strip())
