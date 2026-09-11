"""Признаки различения внутри категории.

Контекст: на рабочей базе 1684 вакансии из 5708 оценок имели ровно 92.0 балла —
скорер награждал попадание в категорию, а профиль пользователя и есть эта категория.
Эти признаки меняются от вакансии к вакансии и разбивают плато.
"""

from __future__ import annotations

from datetime import date

from app.services.vacancy_quality_signals import quality_differentiator_hits

TODAY = date(2026, 9, 11)


def _codes(hits) -> set[str]:
    return {hit.code for hit in hits}


def _weight(hits, code: str) -> int:
    return next(hit.weight for hit in hits if hit.code == code)


# --- свежесть: главный признак, данные есть у 96% вакансий ---

def test_fresh_posting_gets_a_small_bonus() -> None:
    hits = quality_differentiator_hits(
        posted_date=date(2026, 9, 10), vacancy_text="", today=TODAY
    )

    assert _weight(hits, "posting_freshness") == 2


def test_recent_posting_is_neutral_and_produces_no_hit() -> None:
    """Полоса 4-14 дней — обычная вакансия, она не должна ни выигрывать, ни проигрывать."""
    hits = quality_differentiator_hits(
        posted_date=date(2026, 9, 1), vacancy_text="", today=TODAY
    )

    assert "posting_freshness" not in _codes(hits)


def test_freshness_penalty_grows_with_age() -> None:
    def penalty(days: int) -> int:
        hits = quality_differentiator_hits(
            posted_date=date.fromordinal(TODAY.toordinal() - days), vacancy_text="", today=TODAY
        )
        return _weight(hits, "posting_freshness")

    assert penalty(20) == -4
    assert penalty(45) == -8
    assert penalty(120) == -12
    # Монотонность: чем старше, тем хуже, без провалов.
    assert penalty(20) > penalty(45) > penalty(120)


def test_missing_date_is_never_penalized() -> None:
    """Нет данных — это «неизвестно», а не «плохо»."""
    hits = quality_differentiator_hits(posted_date=None, vacancy_text="", today=TODAY)

    assert "posting_freshness" not in _codes(hits)


def test_future_date_is_ignored_rather_than_rewarded() -> None:
    hits = quality_differentiator_hits(
        posted_date=date(2026, 12, 1), vacancy_text="", today=TODAY
    )

    assert "posting_freshness" not in _codes(hits)


# --- оплата ---

def test_stated_salary_is_rewarded() -> None:
    for text in ("15,50 € pro Stunde", "Stundenlohn nach Vereinbarung", "Gehalt: 2600 EUR"):
        hits = quality_differentiator_hits(posted_date=None, vacancy_text=text, today=TODAY)
        assert "salary_stated" in _codes(hits), text


def test_text_without_pay_information_gets_no_salary_hit() -> None:
    hits = quality_differentiator_hits(
        posted_date=None, vacancy_text="Lagerhelfer gesucht, Schichtarbeit, ab sofort.", today=TODAY
    )

    assert "salary_stated" not in _codes(hits)


# --- тип договора ---

def test_permanent_contract_beats_fixed_term() -> None:
    permanent = quality_differentiator_hits(
        posted_date=None, vacancy_text="unbefristeter Arbeitsvertrag", today=TODAY
    )
    fixed = quality_differentiator_hits(
        posted_date=None, vacancy_text="zunächst befristet auf 12 Monate", today=TODAY
    )

    assert _weight(permanent, "permanent_contract") > 0
    assert _weight(fixed, "fixed_term_contract") < 0


def test_unbefristet_is_not_read_as_befristet() -> None:
    """"unbefristet" содержит "befristet" как подстроку — классическая ловушка."""
    hits = quality_differentiator_hits(
        posted_date=None, vacancy_text="Wir bieten einen unbefristeten Vertrag.", today=TODAY
    )

    assert "permanent_contract" in _codes(hits)
    assert "fixed_term_contract" not in _codes(hits)


# --- форма полосы в целом ---

def test_band_is_skewed_downwards() -> None:
    """Подходящая вакансия и так у потолка в 100 — различать можно только затуханием."""
    best = quality_differentiator_hits(
        posted_date=date(2026, 9, 11),
        vacancy_text="unbefristet, 16 € pro Stunde",
        today=TODAY,
    )
    worst = quality_differentiator_hits(
        posted_date=date(2026, 1, 1),
        vacancy_text="befristet auf 6 Monate",
        today=TODAY,
    )

    best_total = sum(hit.weight for hit in best)
    worst_total = sum(hit.weight for hit in worst)
    assert best_total == 8
    assert worst_total == -15
    # Вниз полоса должна тянуться заметно сильнее, чем вверх.
    assert abs(worst_total) > best_total


def test_no_signals_at_all_changes_nothing() -> None:
    assert quality_differentiator_hits(posted_date=None, vacancy_text="", today=TODAY) == ()
