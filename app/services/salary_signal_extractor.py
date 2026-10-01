"""Зарплата из текста объявления: сумма, период, брутто/нетто, ставка в час.

Зачем отдельный модуль. До него по зарплате был один булев признак «в тексте
упомянуты деньги» (+3 балла), и вакансия на 12 €/час не отличалась от вакансии на
22 €/час. Сравнивать их нельзя, пока суммы не приведены к одной базе.

Как считается ставка в час:

* «15,50 € pro Stunde» — уже ставка;
* «2 600 € monatlich» — делится на 173 часа (40 часов в неделю по немецкой норме);
* «45.000 € pro Jahr» — делится на 12 и затем на те же 173 часа.

Чего модуль НЕ делает и делать не должен:

* не превращает нетто в брутто. Коэффициент зависит от налогового класса,
  церковного налога и страховки — по тексту объявления он неизвестен. Нетто
  помечается флагом, а сравнение с ориентиром профиля по нетто-сумме не делается:
  выдуманный пересчёт хуже честного «неизвестно»;
* не додумывает зарплату, которой в тексте нет. Отсутствие суммы — это
  «неизвестно», и наказывать за него нельзя: в Германии большинство объявлений
  оплату не указывают.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from app.services.normalization_models import CanonicalVacancyGroup

SalaryPeriod = Literal["hour", "month", "year"]

# Немецкая полная норма: 40 часов в неделю ≈ 173,33 часа в месяц.
_MONTHLY_HOURS = 173.0
_MONTHS_PER_YEAR = 12

# Границы правдоподобия. Нужны, чтобы не принять за зарплату номер дома, индекс
# или «500 Mitarbeiter»: период определяется по соседним словам, а величина —
# проверкой диапазона.
_PLAUSIBLE_HOURLY = (8.0, 200.0)
_PLAUSIBLE_MONTHLY = (450.0, 30_000.0)
_PLAUSIBLE_YEARLY = (8_000.0, 400_000.0)

_AMOUNT = r"(?P<amount>\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_CURRENCY = r"(?:€|\beur\b|\beuro\b)"

_HOUR_WORDS = r"(?:pro\s+stunde|je\s+stunde|/\s*stunde|stundenlohn|stundensatz|per\s+hour|/\s*h\b|\bp\.?h\b|die\s+stunde)"
_MONTH_WORDS = r"(?:pro\s+monat|je\s+monat|/\s*monat|monatlich|monatsgehalt|monatslohn|per\s+month|brutto/monat)"
_YEAR_WORDS = r"(?:pro\s+jahr|je\s+jahr|/\s*jahr|jahrlich|jahresgehalt|per\s+year|per\s+annum|p\.?a\.?\b)"

# Пара «сумма + период», в обоих порядках слов и с валютой по любую сторону.
_PERIOD_PATTERNS: tuple[tuple[SalaryPeriod, re.Pattern[str]], ...] = (
    (
        "hour",
        re.compile(rf"{_AMOUNT}\s*{_CURRENCY}?\s*(?:{_HOUR_WORDS})", re.IGNORECASE),
    ),
    (
        "hour",
        re.compile(rf"(?:{_HOUR_WORDS})[^0-9]{{0,20}}{_AMOUNT}\s*{_CURRENCY}?", re.IGNORECASE),
    ),
    (
        "month",
        re.compile(rf"{_AMOUNT}\s*{_CURRENCY}?\s*(?:{_MONTH_WORDS})", re.IGNORECASE),
    ),
    (
        "month",
        re.compile(rf"(?:{_MONTH_WORDS})[^0-9]{{0,20}}{_AMOUNT}\s*{_CURRENCY}?", re.IGNORECASE),
    ),
    (
        "year",
        re.compile(rf"{_AMOUNT}\s*{_CURRENCY}?\s*(?:{_YEAR_WORDS})", re.IGNORECASE),
    ),
    (
        "year",
        re.compile(rf"(?:{_YEAR_WORDS})[^0-9]{{0,20}}{_AMOUNT}\s*{_CURRENCY}?", re.IGNORECASE),
    ),
)

# Сумма с валютой, но без названия периода. Период тогда угадывается по величине:
# 15 € — это час, 2600 € — месяц, 45000 € — год. Отдельный путь, потому что
# большинство объявлений пишут именно так.
_BARE_AMOUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_AMOUNT}\s*{_CURRENCY}", re.IGNORECASE),
    re.compile(rf"{_CURRENCY}\s*{_AMOUNT}", re.IGNORECASE),
)

_GROSS_RE = re.compile(r"\bbrutto\b|\bgross\b", re.IGNORECASE)
_NET_RE = re.compile(r"\bnetto\b|\bnet\b|\bauf\s+die\s+hand\b", re.IGNORECASE)
# Любое упоминание оплаты — сохраняет прежний булев признак «деньги названы».
_ANY_PAY_MENTION_RE = re.compile(
    rf"{_CURRENCY}|\bgehalt\b|\bstundenlohn\b|\bverdienst\b|\bbezahlung\b|\bvergutung\b|\blohn\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SalaryEvidence:
    source_record_key: str
    source_url: str | None
    field: Literal["title", "body", "metadata.salary"]
    raw_amount_eur: float
    period: SalaryPeriod
    hourly_eur: float
    is_net: bool


@dataclass(frozen=True, slots=True)
class SalarySignals:
    """Что объявление сказало об оплате.

    `hourly_eur` = None означает «сумму определить не удалось», а не «ноль».
    """

    mentioned: bool = False
    hourly_eur: float | None = None
    period: SalaryPeriod | None = None
    raw_amount_eur: float | None = None
    is_net: bool = False
    is_gross: bool = False
    evidence: tuple[SalaryEvidence, ...] = ()
    conflict: bool = False

    @property
    def is_comparable(self) -> bool:
        """Можно ли сравнивать эту сумму с ориентиром профиля.

        Нетто сравнивать нельзя: пересчёт в брутто по тексту объявления
        невосстановим, а сравнивать нетто с брутто-ориентиром — значит занижать
        вакансию на треть без всяких оснований.
        """
        return self.hourly_eur is not None and not self.is_net


def extract_salary_signals(text: str | None) -> SalarySignals:
    if not text or not text.strip():
        return SalarySignals()

    mentioned = bool(_ANY_PAY_MENTION_RE.search(text))
    is_net = bool(_NET_RE.search(text))
    is_gross = bool(_GROSS_RE.search(text))

    best = _find_amount_with_period(text)
    if best is None:
        best = _find_bare_amount(text)
    if best is None:
        return SalarySignals(mentioned=mentioned, is_net=is_net, is_gross=is_gross)

    period, amount = best
    return SalarySignals(
        mentioned=True,
        hourly_eur=_to_hourly(amount, period),
        period=period,
        raw_amount_eur=amount,
        is_net=is_net,
        is_gross=is_gross,
    )


def extract_canonical_salary_signals(canonical: CanonicalVacancyGroup, *, analysis_text: str) -> SalarySignals:
    """Preserve the existing conservative text policy and expose field-level conflicts.

    Scoring uses the lowest textual amount (explicit periods before inferred ones).
    Adapter salary metadata has never overridden title/body in that policy; retain
    it as evidence only, since it may be estimated or lack a reliable period.
    """
    evidence: list[SalaryEvidence] = []
    for record in canonical.source_records:
        fields: list[tuple[Literal["title", "body", "metadata.salary"], str | None]] = [
            ("title", record.original_title), ("body", record.body_text),
        ]
        metadata = record.raw_payload.get("salary") if isinstance(record.raw_payload, dict) else None
        if isinstance(metadata, str):
            fields.append(("metadata.salary", metadata))
        for field, text in fields:
            salary = extract_salary_signals(text)
            if salary.hourly_eur is None or salary.raw_amount_eur is None or salary.period is None:
                continue
            evidence.append(SalaryEvidence(
                source_record_key=record.source_record_key, source_url=record.source_url,
                field=field, raw_amount_eur=salary.raw_amount_eur, period=salary.period,
                hourly_eur=salary.hourly_eur, is_net=salary.is_net,
            ))
    return replace(
        extract_salary_signals(analysis_text), evidence=tuple(evidence),
        conflict=len({(round(item.hourly_eur, 2), item.is_net) for item in evidence}) > 1,
    )


def _find_amount_with_period(text: str) -> tuple[SalaryPeriod, float] | None:
    """Сумма, рядом с которой прямо назван период. Берётся наименьшая.

    Наименьшая, а не первая: объявления пишут вилку («15,00 € bis 18,00 €»), и
    нижняя граница — то, на что человек может рассчитывать наверняка.
    """
    found: list[tuple[SalaryPeriod, float]] = []
    for period, pattern in _PERIOD_PATTERNS:
        for match in pattern.finditer(text):
            amount = _parse_amount(match.group("amount"))
            if amount is not None and _is_plausible(amount, period):
                found.append((period, amount))
    if not found:
        return None
    return min(found, key=lambda pair: pair[1])


def _find_bare_amount(text: str) -> tuple[SalaryPeriod, float] | None:
    """Сумма с валютой, но без периода: период выводится из величины."""
    found: list[tuple[SalaryPeriod, float]] = []
    for pattern in _BARE_AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            amount = _parse_amount(match.group("amount"))
            if amount is None:
                continue
            period = _guess_period(amount)
            if period is not None:
                found.append((period, amount))
    if not found:
        return None
    return min(found, key=lambda pair: pair[1])


def _guess_period(amount: float) -> SalaryPeriod | None:
    if _PLAUSIBLE_HOURLY[0] <= amount <= 100.0:
        return "hour"
    if _PLAUSIBLE_MONTHLY[0] <= amount <= 15_000.0:
        return "month"
    if _PLAUSIBLE_YEARLY[0] <= amount <= _PLAUSIBLE_YEARLY[1]:
        return "year"
    return None


def _is_plausible(amount: float, period: SalaryPeriod) -> bool:
    low, high = {
        "hour": _PLAUSIBLE_HOURLY,
        "month": _PLAUSIBLE_MONTHLY,
        "year": _PLAUSIBLE_YEARLY,
    }[period]
    return low <= amount <= high


def _to_hourly(amount: float, period: SalaryPeriod) -> float:
    if period == "hour":
        return amount
    if period == "month":
        return amount / _MONTHLY_HOURS
    return amount / _MONTHS_PER_YEAR / _MONTHLY_HOURS


def _parse_amount(raw: str) -> float | None:
    """Немецкая запись числа: точка и пробел — разряды, запятая — дробная часть."""
    cleaned = raw.strip().replace(" ", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(".") == 1:
        integer_part, _, fraction = cleaned.partition(".")
        # "45.000" — это разряды, "15.50" — дробная часть: решает длина хвоста.
        if len(fraction) == 3:
            cleaned = integer_part + fraction
    else:
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None
