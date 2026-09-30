"""Форма занятости, самозанятость и физическая нагрузка из текста объявления.

Три вещи, которые до этого нигде не читались, хотя человек о них спрашивает в
первую очередь.

1. Форма занятости. «Vollzeit», «Teilzeit», «Minijob», «Zeitarbeit» — четыре
   разных договора, и профиль, которому нужна полная занятость, не должен
   получать мини-джоб на 538 евро наравне с ней.

2. Самозанятость. «auf selbstständiger Basis», «Gewerbeschein erforderlich»,
   «als Subunternehmer» — это НЕ трудоустройство: нет отпуска, больничного и
   социальных отчислений. Раньше такое объявление проходило как обычная вакансия.

3. Тяжёлая физическая нагрузка. «Heben bis 25 kg», «körperlich anstrengend» —
   поле physical_work_ok в профиле существовало, но текст вакансии по нему не
   проверялся ни разу.

Отсутствие признака здесь всегда означает «не сказано», а не «нет»: решения по
молчанию принимает вызывающий код, и он обязан отличать одно от другого.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.signal_negation import is_negated_signal, normalize_signal_text

# Формы занятости. Значения совпадают с тем, что хранит профиль, чтобы сравнение
# было прямым, без таблицы перевода.
EMPLOYMENT_FULL_TIME = "full_time"
EMPLOYMENT_PART_TIME = "part_time"
EMPLOYMENT_MINI_JOB = "mini_job"
EMPLOYMENT_TEMPORARY = "temporary"

EMPLOYMENT_TYPE_LABELS_RU: dict[str, str] = {
    EMPLOYMENT_FULL_TIME: "полная занятость",
    EMPLOYMENT_PART_TIME: "частичная занятость",
    EMPLOYMENT_MINI_JOB: "мини-джоб",
    EMPLOYMENT_TEMPORARY: "временная работа или Zeitarbeit",
}
EMPLOYMENT_TYPE_ORDER: tuple[str, ...] = (
    EMPLOYMENT_FULL_TIME,
    EMPLOYMENT_PART_TIME,
    EMPLOYMENT_MINI_JOB,
    EMPLOYMENT_TEMPORARY,
)

_EMPLOYMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (EMPLOYMENT_MINI_JOB, re.compile(r"\bminijob\w*\b|\bmini job\b|\bgeringfugige?\s+beschaftigung\b|\b538\s*euro\s*job\b")),
    (EMPLOYMENT_PART_TIME, re.compile(r"\bteilzeit\w*\b|\bpart time\b|\bstundenweise\b")),
    (EMPLOYMENT_FULL_TIME, re.compile(r"\bvollzeit\w*\b|\bfull time\b|\bganztags\b")),
    (
        EMPLOYMENT_TEMPORARY,
        re.compile(
            r"\bzeitarbeit\w*\b|\bleiharbeit\w*\b|\barbeitnehmeruberlassung\b|"
            r"\bpersonaldienstleist\w*\b|\bsaisonarbeit\w*\b|\baushilfsjob\w*\b|\btemporary\b"
        ),
    ),
)

# Самозанятость. Каждая форма — про то, что договор будет не трудовым.
_SELF_EMPLOYMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "selbstständig" после свёртывания умлаутов даёт "selbststandig" — с двумя
    # "st" подряд. Опциональная "s" покрывает и правильное написание, и частую
    # опечатку "selbstandig".
    ("selbstständige Basis", re.compile(r"\bselbst(?:st)?andig\w*\b|\bfreiberuflich\w*\b")),
    ("Gewerbeschein", re.compile(r"\bgewerbeschein\w*\b|\bgewerbeanmeldung\b|\beigenes\s+gewerbe\b")),
    ("Subunternehmer", re.compile(r"\bsubunternehmer\w*\b|\bsub\s+unternehmer\b|\bnachunternehmer\w*\b")),
    ("Honorarbasis", re.compile(r"\bhonorarbasis\b|\bauf\s+honorarbasis\b|\bhonorarvertrag\w*\b")),
    ("Werkvertrag", re.compile(r"\bwerkvertrag\w*\b|\bdienstvertrag\s+statt\s+arbeitsvertrag\b")),
    ("freelance", re.compile(r"\bfreelanc\w*\b|\bindependent contractor\b|\bself employed\b|\b1099\b")),
)
# Явное трудоустройство. Нужно, чтобы «Festanstellung, keine Subunternehmer»
# не читалось как приглашение оформить Gewerbe.
_EMPLOYED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfestanstellung\b|\bfest\s+angestellt\b|\bin\s+festanstellung\b"),
    re.compile(r"\bunbefristeter\s+arbeitsvertrag\b|\barbeitsvertrag\b"),
    re.compile(r"\bsozialversicherungspflichtig\w*\b"),
    re.compile(r"\btarifvertrag\w*\b|\btariflich\w*\b"),
)
# Тяжёлая физическая нагрузка. Взяты формы, которые говорят именно о нагрузке, а
# не о том, что работа «активная».
_HEAVY_PHYSICAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("körperlich anstrengend", re.compile(r"\bkorperlich\w*\s+(?:anstrengend|belastbar|fordernd|schwer)\w*\b")),
    ("körperliche Belastbarkeit", re.compile(r"\bkorperliche\s+belastbarkeit\b|\bkorperliche\s+fitness\b")),
    ("schwere körperliche Arbeit", re.compile(r"\bschwere\s+korperliche\s+arbeit\b|\bschwerer\s+korperlicher\s+einsatz\b")),
    ("Heben und Tragen", re.compile(r"\bheben\s+und\s+tragen\b|\btragen\s+schwerer\s+lasten\b|\blastenhandling\b")),
    ("подъём тяжестей с указанным весом", re.compile(r"\b(?:bis\s+zu\s+)?(?:1[5-9]|[2-9]\d)\s*kg\b")),
    ("Dauerstehen", re.compile(r"\bdauerstehen\b|\bstandiges\s+stehen\b|\bganztags\s+stehen\b")),
    ("heavy lifting", re.compile(r"\bheavy lifting\b|\bphysically demanding\b")),
)


@dataclass(frozen=True, slots=True)
class EmploymentSignals:
    """Что объявление сообщило о форме занятости и нагрузке.

    Пустые кортежи и False означают «в тексте не сказано».
    """

    employment_types: tuple[str, ...] = ()
    self_employment_signals: tuple[str, ...] = ()
    employed_contract_signals: tuple[str, ...] = ()
    heavy_physical_signals: tuple[str, ...] = ()

    @property
    def requires_self_employment(self) -> bool:
        """Самозанятость названа и в тексте нет признаков трудового договора.

        Объявление кадрового агентства нередко упоминает и то и другое («Wir
        vermitteln in Festanstellung, auch für Selbstständige»). Тогда это не
        обязательное условие, а один из вариантов, и прятать вакансию не за что.
        """
        return bool(self.self_employment_signals) and not self.employed_contract_signals


def extract_employment_signals(text: str | None) -> EmploymentSignals:
    normalized = normalize_signal_text(text)
    if not normalized:
        return EmploymentSignals()

    return EmploymentSignals(
        employment_types=_matching_labels(normalized, _EMPLOYMENT_PATTERNS, skip_negated=True),
        self_employment_signals=_matching_labels(
            normalized, _SELF_EMPLOYMENT_PATTERNS, skip_negated=True
        ),
        employed_contract_signals=tuple(
            pattern.pattern for pattern in _EMPLOYED_PATTERNS if any(not is_negated_signal(normalized, m) for m in pattern.finditer(normalized))
        ),
        heavy_physical_signals=_matching_labels(normalized, _HEAVY_PHYSICAL_PATTERNS, skip_negated=True),
    )


def _matching_labels(
    normalized_text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    skip_negated: bool = False,
) -> tuple[str, ...]:
    labels: list[str] = []
    for label, pattern in patterns:
        for match in pattern.finditer(normalized_text):
            if skip_negated and _is_negated(normalized_text, match):
                continue
            labels.append(label)
            break
    return tuple(labels)


def _is_negated(normalized_text: str, match: re.Match[str]) -> bool:
    return is_negated_signal(normalized_text, match)
