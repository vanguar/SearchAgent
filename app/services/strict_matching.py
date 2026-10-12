"""Evidence gates for the opt-in low-language search. Unknown means hidden.

The ordinary filter remains unchanged. Complete source text is a prerequisite,
not a claim that every possible real-world requirement can be inferred.
"""
from __future__ import annotations

import re
from dataclasses import replace

from app.services.license_requirement_signals import covered_license_classes
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.profile_condition_review import condition_review_hits
from app.services.role_family import DRIVING_LIKE_FAMILIES, classify_vacancy_de
from app.services.rule_catalog import (
    STRONG_EXPERIENCE_RULE,
    _has_analyzable_body,
    _match_requested_cities,
    _requirement_is_mandatory,
    build_raw_analysis_text,
)
from app.services.search_models import RuleHit, SearchProfileContext, VacancySignalSnapshot
from app.services.search_normalizer import parse_search_cities
from app.services.signal_negation import is_negated_signal, mask_negated_signals, normalize_signal_text

_NO_GERMAN = re.compile(
    r"\b(?:deutsch(?:kenntnisse)?\s+(?:sind\s+|ist\s+)?nicht\s+(?:erforderlich|notwendig)|"
    r"keine\s+deutschkenntnisse\s+(?:erforderlich|notwendig)|"
    r"german\s+(?:is\s+)?not\s+(?:required|necessary)|no\s+german\s+required|"
    r"ohne\s+deutsch(?:kenntnisse)?\s+(?:moglich|arbeiten))\b"
)
_A1 = re.compile(r"\b(?:deutsch\w*\s+(?:auf\s+)?(?:niveau\s+)?a1|a1\s+(?:niveau\s+)?deutsch\w*)\b")
_HIGHER_GERMAN = re.compile(
    r"\bdeutsch\w*(?:\s+\w+){0,4}\s+(?:a2|b1|b2|c1|c2)\b|"
    r"\b(?:a2|b1|b2|c1|c2)(?:\s+\w+){0,3}\s+deutsch\w*\b"
)
_ENGLISH_ONLY = re.compile(r"\b(?:english\s+only|only\s+english|nur\s+englisch)\b")
_OTHER_LANGUAGE = re.compile(r"\b(?:englisch\w*|english|polnisch\w*|polish|franzosisch\w*|french)\b")
_LANGUAGE_OPTIONAL = re.compile(
    r"\b(?:von vorteil|wunschenswert|optional|preferred|a plus|nice to have|"
    r"courses?|classes|sprachkurs\w*|unterricht)\b"
)


def strict_matching_rejections(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
    signals: VacancySignalSnapshot,
    *,
    review_hits: tuple[RuleHit, ...] = (),
    worldwide_search: bool = False,
) -> tuple[RuleHit, ...]:
    if profile.strict_match_mode == "off":
        return ()
    hits: list[RuleHit] = []

    def reject(code: str, label: str) -> None:
        hits.append(RuleHit(code=f"strict_{code}", label_ru=label))

    if profile.strict_match_mode not in {"no_german", "a1"}:
        reject("invalid_mode", "неизвестный режим строгого отбора — проверьте профиль")
    if signals.description_insufficient:
        reject("description_unconfirmed", "строгий отбор: полное описание не получено, требования не подтверждены")
    if not any(record.source_url for record in canonical.source_records):
        reject("source_unconfirmed", "строгий отбор: нет ссылки на исходную вакансию")

    cities = profile.search_cities or parse_search_cities(
        ", ".join(profile.preferred_locations),
    )
    matched_city, outside, _ = _match_requested_cities(canonical, replace(profile, search_cities=cities))
    if worldwide_search or not cities or matched_city is None or outside:
        reject("location_unconfirmed", "строгий отбор: место работы в выбранных городах не подтверждено")

    if not (profile.desired_roles or profile.search_query_terms):
        reject("roles_unset", "строгий отбор: укажите подходящие профессии в профиле")
    elif not (signals.role_confirmed is True or signals.desired_role_hits_in_title):
        reject("role_unconfirmed", "строгий отбор: соответствие профессии не подтверждено")

    text = normalize_signal_text(build_raw_analysis_text(canonical))
    # An incomplete source cannot supply positive proof on behalf of a complete
    # record from another source after deduplication. Contradictions still use all text.
    evidence_text = normalize_signal_text("; ".join(
        record.body_text or "" for record in canonical.source_records
        if _has_analyzable_body(replace(canonical, source_records=(record,)))
    ))
    no_german = any(
        (_NO_GERMAN.search(clause) or re.fullmatch(r"\s*(?:auch )?ohne deutsch(?:kenntnisse)?\s*", clause))
        and not re.search(r"\b(?:nicht moglich|nicht ohne|unmoglich|not possible)\b", clause)
        for clause in evidence_text.split(";")
    )
    a1 = bool(_A1.search(evidence_text))
    # An explicit A1 policy cannot silently grant the person language ability.
    known_german = (profile.german_level or "").strip().casefold()
    can_use_a1 = profile.strict_match_mode == "a1" and known_german in {
        "a1", "a2", "b1", "b2", "c1", "c2", "basic", "базовый", "начальный",
    }
    higher_german = signals.strong_german_required or bool(_HIGHER_GERMAN.search(text))
    if (
        higher_german or (a1 and not can_use_a1) or (signals.basic_german_signal and not a1)
        or (signals.german_any_required and not (can_use_a1 and a1))
    ):
        reject("german_mismatch", "строгий отбор: требование немецкого выше допустимого или его уровень не уточнён")
    elif not (no_german or (can_use_a1 and a1)):
        reject("language_unconfirmed", "строгий отбор: явно не подтверждено, что можно без немецкого или с выбранным уровнем")

    if profile.no_usable_english and (signals.english_required_signal or _ENGLISH_ONLY.search(text)):
        reject("english_mismatch", "строгий отбор: требуется английский, владение им не подтверждено")
    for clause in text.split(";"):
        if _LANGUAGE_OPTIONAL.search(clause):
            continue
        for match in _OTHER_LANGUAGE.finditer(clause):
            if is_negated_signal(clause, match):
                continue
            if match.group().startswith(("english", "englisch")) and not profile.no_usable_english:
                continue
            code = "english_mismatch" if match.group().startswith(("english", "englisch")) else "other_language"
            reject(code, "строгий отбор: обязательный иностранный язык не подтверждён в анкете")
            break

    if not profile.work_authorized or signals.work_authorization_risks or signals.sponsorship_ambiguity:
        reject("work_authorization", "строгий отбор: условия допуска к работе не подтверждены")
    experience_text = mask_negated_signals(text, re.compile(r"\b(?:berufs)?erfahrung\w*|\bexperience\b"))
    experience_required = _requirement_is_mandatory(experience_text, STRONG_EXPERIENCE_RULE)
    if signals.degree_required or signals.vocational_training_required or experience_required:
        reject("qualification", "строгий отбор: обязательный диплом, Ausbildung или опыт не подтверждены в анкете")
    if signals.apprenticeship_signal:
        reject("apprenticeship", "строгий отбор: предлагается обучение вместо работы")
    if any(status == "required" for _, status in signals.equipment_permits):
        reject("equipment_permit", "строгий отбор: обязательный допуск к технике не подтверждён в анкете")

    if classify_vacancy_de(canonical.normalized_title) in DRIVING_LIKE_FAMILIES:
        required = set(signals.required_driver_license_categories) | set(signals.title_driver_license_categories)
        alternatives = set(signals.allowed_driver_license_categories)
        covered = covered_license_classes(profile.license_classes)
        if not required and not alternatives:
            reject("license_unconfirmed", "строгий отбор: для работы водителем не подтверждена подходящая категория прав")
        elif (required - covered - alternatives) or (alternatives and not alternatives.intersection(covered)):
            reject("license_mismatch", "строгий отбор: категории прав не соответствуют требованиям вакансии")

    if profile.self_employment_ok is False and not signals.employed_contract_signal:
        reject("contract_unconfirmed", "строгий отбор: работа по найму не подтверждена")
    if profile.employment_types and not set(profile.employment_types).intersection(signals.employment_types):
        reject("employment_unconfirmed", "строгий отбор: выбранная форма занятости не подтверждена")
    if profile.shift_ok is False and signals.shift_signal:
        reject("shift_mismatch", "строгий отбор: сменная работа не соответствует профилю")

    # A driving licence never proves ownership of a car.
    if profile.car_available is not True:
        hits.extend(RuleHit(code=f"strict_{hit.code}", label_ru=f"строгий отбор: {hit.label_ru}") for hit in (
            condition_review_hits(text, replace(profile, car_available=False), immediate_start=False)
        ))
    hits.extend(RuleHit(code=f"strict_{hit.code}", label_ru=f"строгий отбор: {hit.label_ru}") for hit in review_hits)
    return tuple(hits)
