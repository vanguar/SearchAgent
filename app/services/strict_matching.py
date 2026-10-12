"""Opt-in low-language gates: conflicts are hidden, bounded uncertainty is reviewed."""
from __future__ import annotations

import re
from dataclasses import replace

from app.services.geo_distance import canonical_city, is_known_place, location_matches_city
from app.services.hashers import normalize_text_for_fingerprint
from app.services.license_requirement_signals import covered_license_classes
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.profile_condition_review import condition_review_hits
from app.services.role_family import DRIVING_LIKE_FAMILIES, RoleFamily, classify_role_families, classify_vacancy_de
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
from app.services.warehouse_transport import warehouse_route_hits

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
_REQUIREMENTS_SECTION = re.compile(
    r"\b(?:ihr profil|dein profil|bringen sie mit|bringst du mit|wymagania|requirements)\b|"
    r"\b(?:unsere|ihre|deine|fachliche|persönliche)\s+(?:anforderungen|voraussetzungen|qualifikationen)\b|"
    r"\bqualifikation(?=\s*(?:[:\n]|du hast|sie haben))|"
    r"\banforderungen\s+sie\s+mitbringen\s+sollten\s*:|"
    r"\b(?:anforderungen|voraussetzungen|qualifikationen)(?:\s+(?:an|als)\b[^:]{0,70})?\s*[:*>\n]", re.I,
)
_GERMAN_MENTION = re.compile(r"\b(?:deutsch(?:kenntnisse|kenntnissen|kenntnissen)?|german)\b")
_BASIC_GERMAN = re.compile(
    r"\b(?:grundkenntnisse\s+(?:in\s+)?deutsch|deutsch(?:kenntnisse)?\s+grundkenntnisse|"
    r"(?:einfache|geringe|elementare)\s+deutschkenntnisse|basic\s+german)\b"
)
_POLISH_NO_LANGUAGE = re.compile(r"\bznajomosc jezyka (?:niemieckiego )?nie jest wymagana\b")
_SPECIALIST_EXPERIENCE = re.compile(
    r"\bmehrjahrige\w*\s+(?:berufs)?erfahrung\w*\b|"
    r"\berfahrungen und kenntnisse als\b|\bberufserfahrungen?\s+erwartet\b|"
    r"\bfundierte\s+kenntnisse\b"
)
_WORKPLACE = re.compile(
    r"(?i:\b(?:einsatzort|arbeitsort|standort)(?:\s+befindet\s+sich)?\s*(?::|\bin\b|\bist\b)\s*)"
    r"(?P<place>[A-ZÄÖÜ][\wäöüß]+(?:-[A-ZÄÖÜ][\wäöüß]+)?)"
)
_HEADLINE_WORKPLACE = re.compile(
    r"\|\s*(?P<place>[A-ZÄÖÜ][\wäöüß]+(?:-[A-ZÄÖÜ][\wäöüß]+)?)"
    r"(?=\s+(?:Kennziffer|Referenznummer|Stellen-ID|Job-ID)\b)"
)
_TRUNCATED_LANGUAGE_REQUIREMENT = re.compile(
    r"\bsprachkenntnisse\s+in\s+\w{1,7}\s*(?:…|\.{3})", re.I,
)


def strict_matching_hits(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
    signals: VacancySignalSnapshot,
    *,
    review_hits: tuple[RuleHit, ...] = (),
    worldwide_search: bool = False,
) -> tuple[tuple[RuleHit, ...], tuple[RuleHit, ...]]:
    if profile.strict_match_mode == "off":
        return (), ()
    hits: list[RuleHit] = []
    reviews: list[RuleHit] = []

    def reject(code: str, label: str) -> None:
        hits.append(RuleHit(code=f"strict_{code}", label_ru=label))

    def review(code: str, label: str) -> None:
        reviews.append(RuleHit(code=f"strict_{code}", label_ru=label))

    if profile.strict_match_mode not in {"no_german", "a1"}:
        reject("invalid_mode", "неизвестный режим строгого отбора — проверьте профиль")
    if signals.description_insufficient:
        review("description_unconfirmed", "полное описание не получено — проверить требования в оригинале")
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
    elif not _confirmed_role(canonical, profile):
        reject("role_unconfirmed", "строгий отбор: соответствие профессии не подтверждено")

    raw_text = build_raw_analysis_text(canonical)
    transport_hits, transport_reviews = warehouse_route_hits(
        canonical.normalized_title, "\n".join(r.body_text or "" for r in canonical.source_records), profile,
    )
    hits.extend(transport_hits)
    reviews.extend(transport_reviews)
    text = normalize_signal_text(re.sub(r"[ \t]{2,}", "; ", raw_text))
    # Strict city-only searches also reject nearby towns named as the workplace.
    if not profile.search_radius_km:
        for match in (*_WORKPLACE.finditer(raw_text), *_HEADLINE_WORKPLACE.finditer(raw_text)):
            place = match.group("place")
            if is_known_place(canonical_city(place)) and not any(location_matches_city(place, city) for city in cities):
                reject("workplace_mismatch", f"строгий отбор: место работы по тексту — {place}, вне выбранных городов")
                break
    full_evidence = normalize_signal_text("; ".join(
        record.body_text or "" for record in canonical.source_records
        if _has_analyzable_body(replace(canonical, source_records=(record,)))
    ))
    evidence_text = normalize_signal_text("; ".join(record.body_text or "" for record in canonical.source_records))
    if _TRUNCATED_LANGUAGE_REQUIREMENT.search(raw_text):
        reject("language_requirement_truncated", "языковое требование обрезано — условия не удаётся проверить")
    no_german = any(
        (_NO_GERMAN.search(clause) or _POLISH_NO_LANGUAGE.search(clause)
         or re.fullmatch(r"\s*(?:auch )?ohne deutsch(?:kenntnisse)?\s*", clause))
        and not re.search(r"\b(?:nicht moglich|nicht ohne|unmoglich|not possible)\b", clause)
        for clause in evidence_text.split(";")
    )
    a1 = bool(_A1.search(evidence_text))
    # An explicit A1 policy cannot silently grant the person language ability.
    known_german = (profile.german_level or "").strip().casefold()
    can_use_a1 = profile.strict_match_mode == "a1" and known_german in {
        "a1", "a2", "b1", "b2", "c1", "c2", "basic", "базовый", "начальный",
    }
    basic = bool(_BASIC_GERMAN.search(text))
    higher_german = signals.strong_german_required or bool(_HIGHER_GERMAN.search(text))
    higher_german = higher_german or any(
        not is_negated_signal(text, match) and not _optional_near(text, match)
        for match in re.finditer(r"\b(?:(?:gute|sehr gute|fliessende|sichere)\w*\s+deutsch\w*|fluent german)\b", text)
    )
    # Bare requirements such as "gute Deutschkenntnisse" were missed in snippets.
    unexplained_german = any(
        not is_negated_signal(text, match)
        and not _optional_near(text, match)
        and not _NO_GERMAN.search(text[max(0, match.start() - 35):match.end() + 60])
        and not re.search(r"\bohne\s+$", text[max(0, match.start() - 10):match.start()])
        for match in _GERMAN_MENTION.finditer(text)
    )
    if (
        higher_german or (a1 and not can_use_a1)
        or ((signals.basic_german_signal or basic) and not can_use_a1)
        or ((signals.german_any_required or unexplained_german) and not (can_use_a1 and (a1 or basic)))
    ):
        reject("german_mismatch", "строгий отбор: требование немецкого выше допустимого или его уровень не уточнён")
    elif can_use_a1 and basic and not a1:
        review("basic_german_review", "указан базовый немецкий без уровня — уточнить, достаточно ли A1")
    elif not (no_german or (can_use_a1 and a1)):
        review("language_unconfirmed", "язык не указан — возможность работать без немецкого нужно подтвердить")
        if not any(_REQUIREMENTS_SECTION.search(record.body_text or "") for record in canonical.source_records):
            reject("requirements_unavailable", "строгий отбор: в полученном тексте нет раздела требований для проверки")
    if len(evidence_text.strip()) < 160:
        reject("description_unavailable", "строгий отбор: слишком мало текста для проверки условий")
    if re.search(r"\b(?:w(?:l| )asny dojazd|selbststandig\w* anreise)\b", text):
        review("commute_review", "нужно самостоятельно добираться до работы — проверить транспорт по графику смен")
    if (no_german or a1) and not (_NO_GERMAN.search(full_evidence) or _POLISH_NO_LANGUAGE.search(full_evidence)
                                 or _A1.search(full_evidence) or "ohne deutsch" in full_evidence):
        review("language_evidence_partial", "допустимый язык указан только во фрагменте — проверить полное объявление")

    if profile.no_usable_english and (signals.english_required_signal or _ENGLISH_ONLY.search(text)):
        reject("english_mismatch", "строгий отбор: требуется английский, владение им не подтверждено")
    for clause in text.split(";"):
        for match in _OTHER_LANGUAGE.finditer(clause):
            if is_negated_signal(clause, match) or _optional_near(clause, match):
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
    specialist_experience = any(not _optional_near(experience_text, m) for m in _SPECIALIST_EXPERIENCE.finditer(experience_text))
    if signals.degree_required or signals.vocational_training_required or experience_required or specialist_experience:
        reject("qualification", "строгий отбор: обязательный диплом, Ausbildung или опыт не подтверждены в анкете")
    if signals.apprenticeship_signal:
        reject("apprenticeship", "строгий отбор: предлагается обучение вместо работы")
    if any(status == "required" for _, status in signals.equipment_permits):
        reject("equipment_permit", "строгий отбор: обязательный допуск к технике не подтверждён в анкете")
    if re.search(r"\b(?:staplerfahrer|staplermitarbeiter|gabelstaplerfahrer)\b", canonical.normalized_title):
        reject("equipment_permit", "строгий отбор: работа на погрузчике требует отдельной проверки допуска")

    if classify_vacancy_de(canonical.normalized_title) in DRIVING_LIKE_FAMILIES:
        if re.search(r"\beu\s+(?:fuhrerschein|fahrerlaubnis|driving\s+licen[cs]e)\b", text):
            review("license_document_review", "работодатель указал права ЕС — уточнить принятие вашего водительского удостоверения")
        required = set(signals.required_driver_license_categories) | set(signals.title_driver_license_categories)
        alternatives = set(signals.allowed_driver_license_categories)
        covered = covered_license_classes(profile.license_classes)
        if not required and not alternatives:
            mentioned = set(signals.mentioned_driver_license_categories) - set(signals.negated_driver_license_categories)
            if mentioned.intersection(covered):
                review("license_review", "подходящая категория прав упомянута — уточнить, достаточно ли её для этой работы")
            else:
                reject("license_unconfirmed", "строгий отбор: для работы водителем не подтверждена подходящая категория прав")
        elif (required - covered - alternatives) or (alternatives and not alternatives.intersection(covered)):
            reject("license_mismatch", "строгий отбор: категории прав не соответствуют требованиям вакансии")

    if profile.self_employment_ok is False and not signals.employed_contract_signal:
        review("contract_unconfirmed", "тип договора не указан — подтвердить работу по найму")
    if profile.employment_types and not set(profile.employment_types).intersection(signals.employment_types):
        reject("employment_unconfirmed", "строгий отбор: выбранная форма занятости не подтверждена")
    if profile.shift_ok is False and signals.shift_signal:
        reject("shift_mismatch", "строгий отбор: сменная работа не соответствует профилю")

    # A driving licence never proves ownership of a car.
    if profile.car_available is not True:
        hits.extend(RuleHit(code=f"strict_{hit.code}", label_ru=f"строгий отбор: {hit.label_ru}") for hit in (
            condition_review_hits(text, replace(profile, car_available=False), immediate_start=False)
        ))
    # "Ab sofort" is usually negotiable; retain the existing visible date warning.
    hits.extend(RuleHit(code=f"strict_{hit.code}", label_ru=f"строгий отбор: {hit.label_ru}")
                for hit in review_hits if hit.code != "start_date_review")
    return tuple(hits), tuple(reviews)


def _confirmed_role(canonical: CanonicalVacancyGroup, profile: SearchProfileContext) -> bool:
    requested = set(classify_role_families(profile.desired_roles or profile.search_query_terms)) - {RoleFamily.GENERIC}
    actual = classify_vacancy_de(canonical.normalized_title)
    if actual == RoleFamily.GENERIC:
        return any(len(role) > 6 and re.search(r"\b" + re.escape(normalize_text_for_fingerprint(role)) + r"\b", canonical.normalized_title)
                   for role in profile.desired_roles)
    return actual in requested or bool(actual in DRIVING_LIKE_FAMILIES and requested & DRIVING_LIKE_FAMILIES)


def _optional_near(text: str, match: re.Match[str]) -> bool:
    """Optionality must refer to this requirement, not a distant benefit."""
    suffix = text[match.end():match.end() + 48].split(";")[0]
    prefix = text[max(text.rfind(";", 0, match.start()) + 1, match.start() - 25):match.start()]
    return bool(re.match(r"\s*(?:(?:sind|ist|are|is|kenntnisse)\s+)?(?:von vorteil|wunschenswert(?!\s+sind\b)|optional|preferred|a plus|nice to have|courses?|classes|sprachkurs\w*|unterricht)\b", suffix)
                or re.search(r"\b(?:idealerweise|optional|preferred)\s*$", prefix))
