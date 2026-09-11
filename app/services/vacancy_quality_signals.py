"""Признаки, различающие вакансии ВНУТРИ одной подходящей категории.

Зачем это нужно. Основной скорер отвечает на вопрос «подходит ли роль»: склад,
смены, низкий языковой барьер, без диплома. Но если профиль пользователя целиком
про склад и смены, то эти бонусы получают все подходящие вакансии сразу, и балл
перестаёт различать. На рабочей базе это видно буквально: 1684 вакансии из 5708
оценок имели ровно 92.0 — 29% выдачи в одном значении, с одинаковым пояснением.

Здесь собраны признаки, которые меняются от вакансии к вакансии внутри категории:
свежесть, указана ли оплата, бессрочный или срочный договор.

Важное свойство: полоса смещена ВНИЗ. Подходящая вакансия и так набирает балл у
потолка (100), поэтому любые плюсы там срезаются клампом и ничего не различают.
Различение даёт затухание: свежая вакансия остаётся наверху, а та же самая через
два месяца опускается.

Отсутствие данных никогда не штрафуется — нет даты или нет оплаты в тексте
означает «неизвестно», а не «плохо».
"""

from __future__ import annotations

import re
from datetime import date

from app.services.search_models import RuleHit

# Возрастные пороги в днях и вес каждого диапазона. Верхняя граница включительно.
# Первый диапазон — единственный положительный: реально свежее объявление и правда
# выше шансом, что вакансия ещё открыта.
_FRESHNESS_BANDS: tuple[tuple[int | None, int, str], ...] = (
    (3, 2, "опубликовано в последние дни"),
    (14, 0, ""),
    (30, -4, "объявлению больше двух недель"),
    (60, -8, "объявлению больше месяца"),
    (None, -12, "объявлению больше двух месяцев"),
)

# Явно указанная оплата — сигнал прозрачности работодателя и просто полезная
# информация. В немецких объявлениях её скрывают чаще, чем указывают.
_SALARY_RE = re.compile(
    r"(?:€|\beur\b|\beuro\b)\s*\d|\d[\d .,]*\s*(?:€|\beur\b|\beuro\b)"
    r"|\bgehalt\b|\bstundenlohn\b|\bverdienst\b|\bbezahlung\b",
    re.IGNORECASE,
)
_SALARY_BONUS = 3

# Бессрочный договор объективно лучше срочного для соискателя, и это прямо
# написано в тексте объявления.
_PERMANENT_RE = re.compile(r"\bunbefristet\w*\b", re.IGNORECASE)
_FIXED_TERM_RE = re.compile(r"\bbefristet\w*\b|\bzeitlich\s+befristet\b", re.IGNORECASE)
_PERMANENT_BONUS = 3
_FIXED_TERM_PENALTY = -3


def quality_differentiator_hits(
    *,
    posted_date: date | None,
    vacancy_text: str,
    today: date,
) -> tuple[RuleHit, ...]:
    """Признаки различения для одной вакансии. Пустой кортеж — данных не было."""
    hits: list[RuleHit] = []

    freshness = _freshness_hit(posted_date=posted_date, today=today)
    if freshness is not None:
        hits.append(freshness)

    text = vacancy_text or ""
    if _SALARY_RE.search(text):
        hits.append(
            RuleHit(code="salary_stated", label_ru="оплата указана в объявлении", weight=_SALARY_BONUS)
        )

    # "unbefristet" содержит "befristet" как подстроку, поэтому сначала проверяем его.
    if _PERMANENT_RE.search(text):
        hits.append(
            RuleHit(code="permanent_contract", label_ru="бессрочный договор", weight=_PERMANENT_BONUS)
        )
    elif _FIXED_TERM_RE.search(text):
        hits.append(
            RuleHit(code="fixed_term_contract", label_ru="договор срочный", weight=_FIXED_TERM_PENALTY)
        )

    return tuple(hits)


def _freshness_hit(*, posted_date: date | None, today: date) -> RuleHit | None:
    if posted_date is None:
        return None

    age_days = (today - posted_date).days
    if age_days < 0:
        # Дата из будущего — данным источника не доверяем, но и не наказываем.
        return None

    for upper_bound, weight, label in _FRESHNESS_BANDS:
        if upper_bound is None or age_days <= upper_bound:
            if weight == 0:
                return None
            return RuleHit(code="posting_freshness", label_ru=label, weight=weight)
    return None
