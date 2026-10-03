"""Eligibility policy for the owner's AI automation profiles, independent of keyword bonuses.

The candidate has two years of personal projects, no commercial IT experience,
and lives in Germany. Do not apply this policy to other profession profiles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.ai_tools_profile import is_ai_tools_profile, normalize_multilingual_text
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.search_models import RuleHit, SearchProfileContext

_OPTIONAL = r"\b(?:nice to have|preferred|optional|not required|not mandatory|no mandatory|не обязательно|бажано|не обов язково)\b"
_PERSONAL = r"\b(?:(?:substantial )?personal projects?|pet projects?|власн\w* проект\w*|личн\w* проект\w*)\b"
_ENTRY = _PERSONAL + r"|\b(?:no (?:strict )?commercial experience|no experience|entry level|trainee|mentorship|willing to train|grow into|learning opportunity)\b"
_COMMERCIAL = r"\b(?:commercial|professional|production|продакш\w*|коммерческ\w*|комерціин\w*|комерційн\w*)\b"
_EXPERIENCE = r"\b(?:experience|development|engineering|досвід\w*|опыт\w*|розроб\w*|разработ\w*)\b"
_YEARS = r"\b(\d{1,2})(?:\s+(\d{1,2}))?\s*(?:years?|рок\w*|лет|год\w*)\b"
_SENIOR = r"\b(?:senior|lead|staff|principal|architect)\b"
_RESEARCH = r"\b(?:data scientist|applied scientist|(?:ml|machine learning|deep learning) researcher|computer vision engineer|(?:middle )?ds(?: ai)? engineer)\b"


def _requirement_clauses(raw: str) -> list[str]:
    clauses: list[str] = []
    optional_section = False
    for line in raw.splitlines():
        normalized = normalize_multilingual_text(line)
        if re.fullmatch(r"(?:nice to have|significant advantage|(?:would be a |gets you extra |буде |будет )?(?:plus|points|плюсом)|preferred qualifications)\s*", normalized):
            optional_section = True
        elif re.match(r"(?:requirements|must have|responsibilities|what |we offer|about |вимоги|обов язки|требования)\b", normalized):
            optional_section = False
        if not optional_section:
            clauses.extend(normalize_multilingual_text(c) for c in re.split(r"[.;!•]+", line) if c.strip())
    return clauses


def is_it_candidate_profile(profile: SearchProfileContext) -> bool:
    if is_ai_tools_profile(profile):
        return True
    # Use explicit automation intent, never generic Python/IT or a matching ad.
    return any(re.search(r"\b(?:ai|python|llm) automation (?:developer|specialist|engineer)\b",
                         normalize_multilingual_text(role)) for role in profile.desired_roles)


@dataclass(frozen=True)
class ITEligibility:
    positive_hits: tuple[RuleHit, ...] = ()
    negative_hits: tuple[RuleHit, ...] = ()
    rejection_hits: tuple[RuleHit, ...] = ()
    review_hits: tuple[RuleHit, ...] = ()
    score_ceiling: int = 100


def inspect_it_eligibility(
    canonical: CanonicalVacancyGroup, profile: SearchProfileContext, *, remote_search: bool,
) -> ITEligibility:
    if not is_it_candidate_profile(profile):
        return ITEligibility()
    title = normalize_multilingual_text(canonical.normalized_title)
    # Company names and query text are not candidate requirements. Keep sentence
    # boundaries so an optional/pet-project clause cannot erase another requirement.
    raw = "\n".join(record.body_text or "" for record in canonical.source_records)
    clauses = _requirement_clauses(raw)
    positives: list[RuleHit] = []
    negatives: list[RuleHit] = []
    rejections: list[RuleHit] = []
    reviews: list[RuleHit] = []
    ceiling = 100

    def constrain(code: str, label: str, cap: int, penalty: int, *, reject: bool = False) -> None:
        nonlocal ceiling
        ceiling = min(ceiling, cap)
        hit = RuleHit(code=code, label_ru=label, weight=penalty)
        negatives.append(hit)
        (rejections if reject else reviews).append(hit)

    if any(re.search(_ENTRY, c) for c in clauses):
        positives.append(RuleHit(code="it_entry_path", label_ru="подходят личные проекты или обучение на работе", weight=8))

    experience_cap = 100
    for clause in clauses:
        if re.search(_OPTIONAL, clause) or re.search(r"\bno (?:strict )?(?:commercial |professional )?experience", clause):
            continue
        if re.search(r"\b(?:we have|our company has|our team has)\b", clause):
            continue
        # Explicit alternative is different from personal projects merely being welcome.
        personal_alternative = bool(re.search(_PERSONAL, clause) and re.search(r"\b(?:or|або|или)\b", clause))
        commercial = bool(re.search(_COMMERCIAL, clause)) and not personal_alternative
        years = re.search(_YEARS, clause)
        if years and (re.search(_EXPERIENCE, clause) or commercial or re.search(r"\b(?:ml|ds|data science|architecture)\b", clause)):
            minimum = int(years[1])
            if commercial:
                experience_cap = min(experience_cap, 44 if minimum >= 3 else 55 if minimum >= 2 else 69)
            elif minimum > 2:
                experience_cap = min(experience_cap, 44 if minimum >= 5 else 55)
        if commercial and re.search(r"extensive|several years|багаторічн\w*|многолетн\w*", clause):
            experience_cap = min(experience_cap, 44)
        elif commercial and not years and re.search(r"\b(?:experience|досвід\w*|опыт\w*)\b", clause):
            experience_cap = min(experience_cap, 55)
    if experience_cap < 100:
        constrain("it_experience_gap", "обязательный опыт превышает опыт личных проектов без коммерческой работы",
                  experience_cap, {69: -10, 55: -22, 44: -40}[experience_cap], reject=experience_cap == 44)

    if re.search(_RESEARCH, title):
        constrain("it_research_mismatch", "ядро роли — DS/ML или исследования, а профиль — автоматизация и интеграции",
                  35, -40, reject=True)
    if re.search(r"\b(?:ml|machine learning|data science) engineer\b", title) and any(
        re.search(r"statistics|model training|математич\w*|deep learning|pytorch|tensorflow", c) for c in clauses
    ):
        constrain("it_research_mismatch", "ML-роль требует математического или model-training бэкграунда", 35, -40, reject=True)
    senior_requirement = re.search(_SENIOR, title) or any(
        re.search(_SENIOR, c) and re.search(_COMMERCIAL, c) and re.search(_EXPERIENCE, c) for c in clauses
    )
    if senior_requirement and experience_cap < 100:
        constrain("it_seniority_gap", "старшая роль требует подтверждённого профессионального опыта", 35, -20, reject=True)
    if re.search(r"\b(?:finance|financial|accounting|marketing|marketer|sales|recruiter|hr)\b", title) and not re.search(
        r"\b(?:developer|engineer|automation|integration|tools)\b", title
    ):
        constrain("it_role_mismatch", "AI-инструмент упомянут в роли вне автоматизации и разработки", 35, -35, reject=True)

    if any(not re.search(_OPTIONAL, c) and re.search(
        r"(?:strong|advanced|high command of) sql|(?:strong|production|proven).{0,30}(?:aws|gcp|cloud).{0,20}(?:experience|expertise)"
        r"|(?:react|golang|go).{0,20}(?:experience|expertise) (?:is )?required", c
    ) for c in clauses):
        constrain("it_stack_depth_review", "нужна глубина SQL, frontend/Go или cloud сверх базового стека", 69, -10)

    # A required language level is a review, never a hard veto. Assess each mention
    # separately: a benefit or documentation clause cannot soften a spoken requirement.
    english_level = normalize_multilingual_text(profile.english_level)
    level_rank = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6}
    candidate_rank = level_rank.get(english_level, 2 if profile.no_usable_english else 4)
    required_rank = 0
    for clause in clauses:
        if not re.search(r"english|англіиськ\w*|англійськ\w*|англииск\w*", clause):
            continue
        if re.search(_OPTIONAL, clause) or re.search(r"\b(?:courses?|lessons?|курси|курсы)\b", clause):
            continue
        spoken = bool(re.search(r"spoken|speaking|verbal|communication|client|спілкуван\w*|разговор\w*", clause))
        if not spoken and re.search(r"reading|documentation|written|basic|\ba[12]\b|документац\w*", clause):
            continue
        level = re.search(r"\b([bc][12])\b", clause)
        rank = level_rank[level[1]] if level else 0
        if not rank and re.search(r"fluent|advanced|strong english|вільн\w*|свободн\w*", clause):
            rank = max(rank, 5)
        elif not rank and "intermediate" in clause and "upper intermediate" not in clause:
            rank = 3
        elif not rank and (spoken or re.search(r"upper intermediate|good|required|mandatory|intermediate", clause)):
            rank = max(rank, 4)
        required_rank = max(required_rank, rank)
    if required_rank > candidate_rank:
        constrain("it_english_review", "английский выше уровня кандидата: проверить устное общение",
                  59 if required_rank >= 5 else 69, -25 if required_rank >= 5 else -15 if required_rank >= 4 else -8)

    if remote_search:
        location = normalize_multilingual_text(" ".join(
            [canonical.location_text or "", *(r.original_location or "" for r in canonical.source_records)]
        ))
        ukraine = r"(?:ukraine|украін\w*|украин\w*)"
        kyiv = r"(?:kyiv|kiev|києв\w*|киев\w*)"
        presence = rf"(?:must (?:reside|live|be (?:based|located)) in {ukraine}|ukraine only|(?:обов язков\w*|обязательн\w*).{{0,40}}(?:прожива\w*|проживан\w*).{{0,15}}{ukraine})"
        office = rf"(?:mandatory hybrid.{{0,15}}{kyiv}|office {kyiv}|(?:mandatory|required|обов язков\w*|обязательн\w*).{{0,40}}(?:office|офіс\w*|офис\w*).{{0,15}}{kyiv}|(?:office|hybrid|офіс\w*|офис\w*).{{0,20}}{kyiv}.{{0,25}}(?:mandatory|required))"
        restricted = any(
            not re.search(_OPTIONAL, c) and (re.search(presence, c) or re.search(office, c))
            for c in [*clauses, location]
        )
        if restricted:
            constrain("it_location_mismatch", "обязательно физическое присутствие в Украине: не подходит remote из Германии",
                      35, -40, reject=True)
        elif re.search(ukraine, location) and re.search(r"remote|віддалено|удаленно", location):
            constrain("it_location_review", "удалёнка указана для Украины: уточнить возможность работы из Германии", 69, -5)
        elif re.search(r"remote|віддалено|удаленно", location) and re.search(r"worldwide|europe|\beu\b|germany|німеччин\w*|герман\w*", location):
            positives.append(RuleHit(code="it_remote_fit", label_ru="удалённая работа из Германии совместима с локацией", weight=6))

    return ITEligibility(tuple(positives), tuple(negatives), tuple(rejections), tuple(reviews), ceiling)
