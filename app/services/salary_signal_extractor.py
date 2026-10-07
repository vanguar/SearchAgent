"""Зарплата из текста объявления: сумма, период, брутто/нетто, ставка в час.

Зачем отдельный модуль. До него по зарплате был один булев признак «в тексте
упомянуты деньги» (+3 балла), и вакансия на 12 €/час не отличалась от вакансии на
22 €/час. Сравнивать их нельзя, пока суммы не приведены к одной базе.

Как считается ставка в час:

* «15,50 € pro Stunde» — уже ставка;
* «2 600 € monatlich» — делится на 173 часа (40 часов в неделю по немецкой норме);
* «45.000 € pro Jahr» — делится на 12 и затем на те же 173 часа.

Правила выбора, когда сумм несколько:

* источник по приоритету: текст описания, затем заголовок, затем метаданные
  источника. Максимум не берётся никогда; внутри одного поля — нижняя граница
  вилки, на которую можно рассчитывать наверняка;
* «bis zu», «up to», «до», «max.» — это максимум (часто кликбейт), а не
  расчётная ставка: такая сумма в расчёт не идёт;
* сумма вне правдоподобных границ (пороги в конфиге) в расчёт не идёт, а
  вакансия помечается «сумма сомнительна»: «2900,00 Euro die Stunde» — это
  опечатка, а не ставка;
* «50% Weihnachtsgeld» после «Stundenlohn» — это процент, а не сумма.

Чего модуль НЕ делает и делать не должен:

* не превращает нетто в брутто. Коэффициент зависит от налогового класса,
  церковного налога и страховки — по тексту объявления он неизвестен. Нетто
  помечается флагом, а сравнение с ориентиром профиля по нетто-сумме не делается;
* не додумывает зарплату, которой в тексте нет. Отсутствие суммы — это
  «неизвестно», и наказывать за него нельзя.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.normalization_models import CanonicalVacancyGroup

SalaryPeriod = Literal["hour", "month", "year"]
SalaryField = Literal["title", "body", "metadata.salary"]

# Немецкая полная норма: 40 часов в неделю ≈ 173,33 часа в месяц.
_MONTHLY_HOURS = 173.0
_MONTHS_PER_YEAR = 12

# Число не должно продолжаться процентом или следующей цифрой.
_AMOUNT = r"(?P<amount>\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\s*%)(?![\d.,]\d)"
_CURRENCY = r"(?:€|\beur\b|\beuro\b)"

_HOUR_WORDS = r"(?:pro\s+stunde|je\s+stunde|/\s*stunde|stundenlohn|stundensatz|per\s+hour|/\s*h\b|\bp\.?h\b|die\s+stunde)"
_MONTH_WORDS = r"(?:pro\s+monat|je\s+monat|/\s*monat|monatlich|monatsgehalt|monatslohn|per\s+month|brutto/monat)"
_YEAR_WORDS = r"(?:pro\s+jahr|je\s+jahr|/\s*jahr|jahrlich|jährlich|jahresgehalt|per\s+year|per\s+annum|p\.?a\.?\b)"

# Пара «сумма + период», в обоих порядках слов и с валютой по любую сторону.
# В обратном порядке («Stundenlohn … 15,50 €») валюта обязательна: иначе после
# «Stundenlohn inkl.» подхватывалось любое число из следующей фразы.
_RANGE = r"\s*{currency}?\s*(?:-|–|bis(?!\s+zu))\s*\d[\d.,\s]*\s*{currency}?\s*"
_PERIOD_PATTERNS: tuple[tuple[SalaryPeriod, re.Pattern[str]], ...] = (
    # Нижняя граница вилки «15,00 € bis 18,00 € pro Stunde» получает период верхней.
    ("hour", re.compile(rf"{_AMOUNT}{_RANGE.format(currency=_CURRENCY)}(?:{_HOUR_WORDS})", re.IGNORECASE)),
    ("month", re.compile(rf"{_AMOUNT}{_RANGE.format(currency=_CURRENCY)}(?:{_MONTH_WORDS})", re.IGNORECASE)),
    ("year", re.compile(rf"{_AMOUNT}{_RANGE.format(currency=_CURRENCY)}(?:{_YEAR_WORDS})", re.IGNORECASE)),
    ("hour", re.compile(rf"{_AMOUNT}\s*{_CURRENCY}?\s*(?:{_HOUR_WORDS})", re.IGNORECASE)),
    ("hour", re.compile(rf"(?:{_HOUR_WORDS})[^0-9]{{0,20}}{_AMOUNT}\s*{_CURRENCY}", re.IGNORECASE)),
    ("month", re.compile(rf"{_AMOUNT}\s*{_CURRENCY}?\s*(?:{_MONTH_WORDS})", re.IGNORECASE)),
    ("month", re.compile(rf"(?:{_MONTH_WORDS})[^0-9]{{0,20}}{_AMOUNT}\s*{_CURRENCY}", re.IGNORECASE)),
    ("year", re.compile(rf"{_AMOUNT}\s*{_CURRENCY}?\s*(?:{_YEAR_WORDS})", re.IGNORECASE)),
    ("year", re.compile(rf"(?:{_YEAR_WORDS})[^0-9]{{0,20}}{_AMOUNT}\s*{_CURRENCY}", re.IGNORECASE)),
)

# Сумма с валютой, но без названия периода. Период тогда угадывается по величине.
_BARE_AMOUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Нижняя граница вилки без периода: «2410-3400€».
    re.compile(rf"{_AMOUNT}\s*(?:-|–|bis(?!\s+zu))\s*\d[\d.,]*\s*{_CURRENCY}", re.IGNORECASE),
    re.compile(rf"{_AMOUNT}\s*{_CURRENCY}", re.IGNORECASE),
    re.compile(rf"{_CURRENCY}\s*{_AMOUNT}", re.IGNORECASE),
)

# Сумма названа как верхняя граница: «bis zu 4612,22 Euro», «up to», «до», «max.».
_UPPER_BOUND_BEFORE_RE = re.compile(
    r"(?:\bbis\s+zu|\bup\s+to|\bmax(?:imal)?\.?|\bhöchstens|\bhochstens|(?<!\w)до)\s*(?:€|eur\b|euro\b)?\s*$",
    re.IGNORECASE,
)
_UPPER_BOUND_WINDOW = 20
# Сумма без периода, за которой названа не зарплата: суточные, премии, доплаты.
_NOT_A_WAGE_AFTER_RE = re.compile(
    r"[\s.,]*(?:netto\s*|brutto\s*)?(?:/\s*tag|pro\s+tag|am\s+tag|tages\w*|spesen|pauschale|urlaubsgeld|"
    r"weihnachtsgeld|pr[aä]mie|bonus|zuschlag|zulage|gutschein|einmalig)",
    re.IGNORECASE,
)

_GROSS_RE = re.compile(r"\bbrutto\b|\bgross\b", re.IGNORECASE)
_NET_RE = re.compile(r"\bnetto\w*|\bnet\b|\bauf\s+die\s+hand\b", re.IGNORECASE)
_ANY_PAY_MENTION_RE = re.compile(
    rf"{_CURRENCY}|\bgehalt\b|\bstundenlohn\b|\bverdienst\b|\bbezahlung\b|\bvergutung\b|\blohn\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SalaryEvidence:
    source_record_key: str
    source_url: str | None
    field: SalaryField
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
    # Названа сумма вне правдоподобных границ: в расчёт она не идёт.
    doubtful: bool = False
    doubtful_amounts: tuple[str, ...] = ()
    # Названа только верхняя граница («bis zu …»): расчётной ставки нет.
    upper_bound_only: bool = False
    upper_bound_amount: str | None = None

    @property
    def is_comparable(self) -> bool:
        """Можно ли сравнивать эту сумму с ориентиром профиля (нетто — нельзя)."""
        return self.hourly_eur is not None and not self.is_net and not self.doubtful


@dataclass(frozen=True, slots=True)
class _Amount:
    period: SalaryPeriod
    amount: float
    upper_bound: bool
    plausible: bool


def extract_salary_signals(text: str | None, *, config: RelevanceConfig | None = None) -> SalarySignals:
    if not text or not text.strip():
        return SalarySignals()
    resolved = config or get_relevance_config()

    mentioned = bool(_ANY_PAY_MENTION_RE.search(text))
    is_net = bool(_NET_RE.search(text))
    is_gross = bool(_GROSS_RE.search(text))

    explicit = _amounts_with_period(text, resolved)
    doubtful = tuple(
        f"{_format_amount(item.amount)} €/{_PERIOD_RU[item.period]}"
        for item in explicit if not item.plausible and not item.upper_bound
    )
    candidates = [item for item in explicit if item.plausible and not item.upper_bound]
    if not candidates and not explicit:
        candidates = [item for item in _bare_amounts(text, resolved) if not item.upper_bound]
    upper_bounds = [item for item in (*explicit, *_bare_amounts(text, resolved)) if item.upper_bound]

    if not candidates:
        upper = min(upper_bounds, key=lambda item: item.amount) if upper_bounds else None
        return SalarySignals(
            mentioned=mentioned or bool(explicit),
            is_net=is_net,
            is_gross=is_gross,
            doubtful=bool(doubtful),
            doubtful_amounts=doubtful,
            upper_bound_only=upper is not None,
            upper_bound_amount=(
                f"{_format_amount(upper.amount)} €/{_PERIOD_RU[upper.period]}" if upper is not None else None
            ),
        )

    best = min(candidates, key=lambda item: _to_hourly(item.amount, item.period))
    return SalarySignals(
        mentioned=True,
        hourly_eur=_to_hourly(best.amount, best.period),
        period=best.period,
        raw_amount_eur=best.amount,
        is_net=is_net,
        is_gross=is_gross,
        doubtful=bool(doubtful),
        doubtful_amounts=doubtful,
    )


_FIELD_PRIORITY: tuple[SalaryField, ...] = ("body", "title", "metadata.salary")


def extract_canonical_salary_signals(canonical: CanonicalVacancyGroup, *, analysis_text: str) -> SalarySignals:
    """Ставка вакансии по полям всех её источников.

    Поля перебираются по приоритету (описание → заголовок → метаданные): ставку
    даёт первое поле, где она есть. Сумма вне границ в любом описании или
    заголовке делает всю оплату сомнительной: источники одной вакансии
    расходятся настолько, что верить ни одному числу нельзя.
    `analysis_text` оставлен ради совместимости вызова и признака «оплата упомянута».
    """
    evidence: list[SalaryEvidence] = []
    by_field: dict[SalaryField, list[tuple[SalarySignals, str]]] = {field: [] for field in _FIELD_PRIORITY}
    for record in canonical.source_records:
        fields: list[tuple[SalaryField, str | None]] = [
            ("title", record.original_title), ("body", record.body_text),
        ]
        metadata = record.raw_payload.get("salary") if isinstance(record.raw_payload, dict) else None
        if isinstance(metadata, str):
            fields.append(("metadata.salary", metadata))
        for field, text in fields:
            salary = extract_salary_signals(text)
            by_field[field].append((salary, text or ""))
            if salary.hourly_eur is None or salary.raw_amount_eur is None or salary.period is None:
                continue
            evidence.append(SalaryEvidence(
                source_record_key=record.source_record_key, source_url=record.source_url,
                field=field, raw_amount_eur=salary.raw_amount_eur, period=salary.period,
                hourly_eur=salary.hourly_eur, is_net=salary.is_net,
            ))

    textual = [salary for field in ("body", "title") for salary, _ in by_field[field]]
    doubtful_amounts = tuple(dict.fromkeys(amount for salary in textual for amount in salary.doubtful_amounts))
    upper_bound = next((salary.upper_bound_amount for salary in textual if salary.upper_bound_amount), None)
    mentioned = bool(_ANY_PAY_MENTION_RE.search(analysis_text or "")) or any(
        salary.mentioned for salaries in by_field.values() for salary, _ in salaries
    )
    conflict = len({(round(item.hourly_eur, 2), item.is_net) for item in evidence}) > 1

    chosen: SalarySignals | None = None
    for field in _FIELD_PRIORITY:
        usable = [salary for salary, _ in by_field[field] if salary.hourly_eur is not None]
        if usable:
            chosen = min(usable, key=lambda salary: salary.hourly_eur or 0.0)
            break

    if doubtful_amounts or chosen is None:
        return SalarySignals(
            mentioned=mentioned,
            is_net=any(salary.is_net for salary in textual),
            is_gross=any(salary.is_gross for salary in textual),
            evidence=tuple(evidence),
            conflict=conflict,
            doubtful=bool(doubtful_amounts),
            doubtful_amounts=doubtful_amounts,
            upper_bound_only=chosen is None and upper_bound is not None,
            upper_bound_amount=upper_bound,
        )
    return replace(
        chosen,
        mentioned=True,
        evidence=tuple(evidence),
        conflict=conflict,
        upper_bound_amount=upper_bound,
    )


_PERIOD_RU: dict[SalaryPeriod, str] = {"hour": "ч", "month": "мес", "year": "год"}


def _bounds(config: RelevanceConfig) -> dict[SalaryPeriod, tuple[float, float]]:
    return {
        "hour": (config.salary_hourly_min, config.salary_hourly_max),
        "month": (config.salary_monthly_min, config.salary_monthly_max),
        "year": (config.salary_yearly_min, config.salary_yearly_max),
    }


def _is_upper_bound(text: str, start: int) -> bool:
    return bool(_UPPER_BOUND_BEFORE_RE.search(text[max(0, start - _UPPER_BOUND_WINDOW): start]))


def _amounts_with_period(text: str, config: RelevanceConfig) -> list[_Amount]:
    """Суммы, рядом с которыми прямо назван период. Одна сумма — одна запись."""
    bounds = _bounds(config)
    found: dict[int, _Amount] = {}
    for period, pattern in _PERIOD_PATTERNS:
        for match in pattern.finditer(text):
            amount = _parse_amount(match.group("amount"))
            if amount is None or amount <= 0:
                continue
            start = match.start("amount")
            low, high = bounds[period]
            found.setdefault(start, _Amount(
                period=period, amount=amount,
                upper_bound=_is_upper_bound(text, start),
                plausible=low <= amount <= high,
            ))
    return list(found.values())


def _bare_amounts(text: str, config: RelevanceConfig) -> list[_Amount]:
    """Суммы с валютой без периода: период выводится из величины, остальное отбрасывается."""
    found: dict[int, _Amount] = {}
    for pattern in _BARE_AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            amount = _parse_amount(match.group("amount"))
            if amount is None:
                continue
            period = _guess_period(amount, config)
            if period is None:
                continue
            if _NOT_A_WAGE_AFTER_RE.match(text, match.end()):
                continue
            start = match.start("amount")
            found.setdefault(start, _Amount(
                period=period, amount=amount, upper_bound=_is_upper_bound(text, start), plausible=True,
            ))
    return list(found.values())


def _guess_period(amount: float, config: RelevanceConfig) -> SalaryPeriod | None:
    for period, (low, high) in _bounds(config).items():
        if low <= amount <= high:
            return period
    return None


def _to_hourly(amount: float, period: SalaryPeriod) -> float:
    if period == "hour":
        return amount
    if period == "month":
        return amount / _MONTHLY_HOURS
    return amount / _MONTHS_PER_YEAR / _MONTHLY_HOURS


def _format_amount(amount: float) -> str:
    text = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return text[:-3] if text.endswith(",00") else text


def _parse_amount(raw: str) -> float | None:
    """Немецкая запись числа: точка и пробел — разряды, запятая — дробная часть."""
    cleaned = raw.strip().replace(" ", "").replace(" ", "")
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
