from __future__ import annotations

import re

from app.services.ai_tools_profile import (
    build_ai_tools_match_text,
    build_ai_tools_title_text,
    has_classic_engineering_title,
    has_deep_classic_engineering_requirements,
    is_ai_tools_profile,
)
from app.services.driver_license_signal_extractor import extract_profile_driver_license_categories
from app.services.hashers import normalize_text_for_fingerprint
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.profile_parser import (
    DRIVER_B_FERNVERKEHR_ROLE,
    DRIVER_B_FERNVERKEHR_SEARCH_TERMS,
)
from app.services.role_family import (
    RoleFamily,
    classify_desired_roles,
    classify_vacancy_de,
    families_are_compatible,
    is_specific_family,
)
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import inspect_vacancy
from app.services.search_models import (
    FilterResult,
    RuleHit,
    SearchProfileContext,
    VacancySignalSnapshot,
    normalize_profile_text,
)
from app.services.search_normalizer import is_remote_worldwide_location

# Tokens so distinctive they identify a family even in long body text.
# Deliberately narrow — body text has more noise than job titles, so only
# unambiguous multi-word or compound terms are listed.
_STRONG_BODY_TOKENS: list[tuple[RoleFamily, tuple[str, ...]]] = [
    (RoleFamily.IT, (
        "softwareentwickler", "frontend", "backend", "devops",
        "programmierer", "fullstack", "full stack",
    )),
    (RoleFamily.HEALTHCARE, (
        "pflegedokumentation", "pflegefachkraft",
    )),
    (RoleFamily.KITCHEN, (
        "gastronom", "kuchenhilfe",
    )),
]

_NEGATIVE_HIT_FAMILIES: dict[str, RoleFamily] = {
    "healthcare_family": RoleFamily.HEALTHCARE,
    "engineering_it_family": RoleFamily.IT,
    "office_admin_family": RoleFamily.OFFICE,
    "sales_family": RoleFamily.SALES,
}

_NON_IT_WRITING_TITLE_PHRASES: tuple[str, ...] = (
    "content writer",
    "copy writer",
    "copywriter",
    "freelance writer",
    "technical writer",
    "content editor",
    "seo writer",
    "blog writer",
)

_NON_IT_WRITING_TITLE_TOKENS: frozenset[str] = frozenset({
    "writer",
    "texter",
    "autor",
    "redakteur",
    "journalist",
    "editor",
})

_SCHOOL_TRANSPORT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:schulerverkehr|schulerbeforderung|schulbus(?:fahrer)?|schulfahrdienst|schulertransport)\w*\b"
    ),
    re.compile(r"\bfahrdienst\s+fur\s+schuler\w*\b"),
    re.compile(r"\bbeforderung\s+von\s+schuler\w*\b"),
)


class FilterEngine:
    """Deterministic hard filters for PHASE 7 search results."""

    def evaluate(
        self,
        canonical: CanonicalVacancyGroup,
        profile: SearchProfileContext,
        *,
        signals: VacancySignalSnapshot | None = None,
        search_mode: str | None = None,
    ) -> FilterResult:
        resolved_signals = signals or inspect_vacancy(canonical, profile)

        positive_hits = list(_collect_positive_hits(resolved_signals, profile))
        rejection_hits: list[RuleHit] = []
        review_hits: list[RuleHit] = []

        if resolved_signals.excluded_role_hits:
            rejection_hits.append(RuleHit(code="excluded_role", label_ru="роль исключена профилем"))

        if _is_driver_b_fernverkehr_profile(profile) and _is_school_transport(resolved_signals.combined_text):
            rejection_hits.append(
                RuleHit(code="school_transport_mismatch", label_ru="школьные пассажирские перевозки исключены")
            )

        driver_license_mismatch = _driver_license_mismatch_hit(resolved_signals, profile)
        if driver_license_mismatch is not None:
            rejection_hits.append(driver_license_mismatch)

        heavy_vehicle_mismatch = _heavy_vehicle_mismatch_hit(resolved_signals, profile)
        if heavy_vehicle_mismatch is not None:
            rejection_hits.append(heavy_vehicle_mismatch)

        family_mismatch = _check_profession_family_mismatch(canonical, profile)
        if family_mismatch:
            rejection_hits.append(RuleHit(
                code="profession_family_mismatch",
                label_ru="профессиональная область не совпадает с запросом",
            ))

        if not family_mismatch and _is_non_it_writing_title_for_it_profile(canonical, profile):
            rejection_hits.append(RuleHit(
                code="non_it_writing_title_mismatch",
                label_ru="заголовок явно не IT-разработка",
            ))

        # Skip keyword-level role mismatch when taxonomy already confirmed family compatibility.
        # This prevents false-rejecting IT vacancies whose German titles ("entwickler", "software*")
        # appear in NEGATIVE_ROLE_FAMILIES but whose desired-role keywords are in English.
        if not family_mismatch and _is_clear_role_mismatch(resolved_signals, profile):
            rejection_hits.append(RuleHit(code="clear_role_mismatch", label_ru="роль явно вне целевого профиля"))

        if resolved_signals.strong_german_required and profile.low_german:
            rejection_hits.append(RuleHit(code="strong_german_mismatch", label_ru="явно требуют хороший немецкий"))

        if (
            resolved_signals.german_any_required
            and not resolved_signals.low_language_signal
            and profile.no_german_required
        ):
            rejection_hits.append(RuleHit(code="german_required_mismatch", label_ru="требуется знание немецкого"))

        if is_ai_tools_profile(profile) and resolved_signals.english_required_signal:
            rejection_hits.append(
                RuleHit(
                    code="english_required_mismatch",
                    label_ru="английский явно указан как обязательный",
                )
            )

        if (
            is_ai_tools_profile(profile)
            and has_classic_engineering_title(build_ai_tools_title_text(canonical))
            and has_deep_classic_engineering_requirements(build_ai_tools_match_text(canonical))
        ):
            rejection_hits.append(
                RuleHit(
                    code="classic_engineering_mismatch",
                    label_ru="роль требует глубокой классической software-engineering подготовки",
                )
            )

        if resolved_signals.degree_required:
            target = rejection_hits if profile.low_barrier_focus else review_hits
            target.append(RuleHit(code="degree_mismatch", label_ru="нужен обязательный профильный диплом"))

        if resolved_signals.vocational_training_required:
            target = rejection_hits if profile.low_barrier_focus else review_hits
            target.append(RuleHit(code="vocational_mismatch", label_ru="нужен обязательный Ausbildung"))

        if resolved_signals.strong_experience_required and not resolved_signals.entry_level_signal:
            target = rejection_hits if profile.low_barrier_focus else review_hits
            target.append(RuleHit(code="experience_mismatch", label_ru="просят заметный профильный опыт"))

        # A worldwide-remote run must never hard-reject on geography: the user explicitly
        # opted out of a local filter. Treat the run as worldwide either when the search mode
        # says so or when the profile's preferred locations describe a global/remote search.
        worldwide_search = (
            search_mode == "remote_worldwide"
            or is_remote_worldwide_location(profile.preferred_locations)
        )
        if (
            profile.preferred_locations
            and profile.relocation_ready is False
            and not worldwide_search
            and resolved_signals.location_match is False
        ):
            rejection_hits.append(RuleHit(code="location_mismatch", label_ru="локация не совпадает с профилем"))

        if resolved_signals.sponsorship_ambiguity:
            review_hits.append(RuleHit(code="sponsorship_review", label_ru="есть вопросы по допуску к работе"))

        decision = "allow"
        if rejection_hits:
            decision = "reject"
        elif review_hits:
            decision = "review"

        return FilterResult(
            decision=decision,
            positive_hits=_dedupe_hits(positive_hits),
            rejection_hits=_dedupe_hits(rejection_hits),
            review_hits=_dedupe_hits(review_hits),
        )


def _collect_positive_hits(signals: VacancySignalSnapshot, profile: SearchProfileContext) -> tuple[RuleHit, ...]:
    hits: list[RuleHit] = []
    hits.extend(signals.positive_role_hits)
    hits.extend(signals.desired_role_hits)
    if signals.low_language_signal:
        hits.append(RuleHit(code="low_language_signal", label_ru="языковой барьер невысокий"))
    if signals.shift_signal and profile.accepts_shifts:
        hits.append(RuleHit(code="shift_fit", label_ru="смены допустимы"))
    if signals.location_match:
        hits.append(RuleHit(code="location_fit", label_ru="локация совпадает с профилем"))
    return _dedupe_hits(hits)


def _is_driver_b_fernverkehr_profile(profile: SearchProfileContext) -> bool:
    target_role = normalize_profile_text(DRIVER_B_FERNVERKEHR_ROLE)
    if any(normalize_profile_text(role) == target_role for role in profile.desired_roles):
        return True
    return tuple(profile.search_query_terms) == DRIVER_B_FERNVERKEHR_SEARCH_TERMS


def _is_school_transport(combined_text: str) -> bool:
    return any(pattern.search(combined_text) for pattern in _SCHOOL_TRANSPORT_PATTERNS)


def _driver_license_mismatch_hit(
    signals: VacancySignalSnapshot,
    profile: SearchProfileContext,
) -> RuleHit | None:
    profile_categories = set(extract_profile_driver_license_categories(profile.driver_license))
    if profile_categories != {"B"}:
        return None

    incompatible_categories = tuple(
        category
        for category in signals.mentioned_driver_license_categories
        if category != "B"
    )
    if not incompatible_categories:
        return None

    mentioned = "/".join(incompatible_categories)
    if incompatible_categories == ("LKW",):
        label = "в вакансии упоминается LKW-Führerschein, а профиль ограничен категорией B"
    elif len(incompatible_categories) == 1:
        label = f"в вакансии упоминается категория {mentioned}, а в профиле указана только категория B"
    else:
        label = f"в вакансии упоминаются категории {mentioned}, а профиль ограничен категорией B"
    return RuleHit(
        code="driver_license_mismatch",
        label_ru=label,
    )


def _heavy_vehicle_mismatch_hit(
    signals: VacancySignalSnapshot,
    profile: SearchProfileContext,
) -> RuleHit | None:
    if not _is_b_only_driving_profile(profile):
        return None

    detected = signals.heavy_vehicle_signals or signals.heavy_driver_qualification_signals
    if not detected:
        return None

    # An explicit heavy signal dominates a simultaneous light-commercial signal.
    # Genuine negations (for example "kein LKW, nur Sprinter") are removed by the extractor.
    return RuleHit(
        code="heavy_vehicle_mismatch",
        label_ru=(
            "Вакансия относится к тяжёлому/специализированному транспорту, "
            "а профиль ограничен автомобилями категории B до 3,5 т. "
            f"Обнаружен сигнал: {detected[0]}."
        ),
    )


def _is_b_only_driving_profile(profile: SearchProfileContext) -> bool:
    if set(extract_profile_driver_license_categories(profile.driver_license)) != {"B"}:
        return False
    if _is_driver_b_fernverkehr_profile(profile):
        return True

    for role_text in (*profile.desired_roles, *profile.search_query_terms):
        intent = normalize_role_intent(role_text)
        if intent is not None and intent.family is RoleFamily.DRIVING:
            return True
    return RoleFamily.DRIVING in classify_desired_roles(profile.desired_roles)


def _is_clear_role_mismatch(signals: VacancySignalSnapshot, profile: SearchProfileContext) -> bool:
    if signals.excluded_role_hits:
        return True
    if not signals.negative_role_hits:
        return False
    if signals.positive_role_hits or signals.desired_role_hits:
        return False
    if _negative_hits_are_compatible_with_profile(signals, profile):
        return False
    return profile.low_barrier_focus or bool(profile.desired_roles)


def _negative_hits_are_compatible_with_profile(
    signals: VacancySignalSnapshot,
    profile: SearchProfileContext,
) -> bool:
    query_families = {family for family in classify_desired_roles(profile.desired_roles) if is_specific_family(family)}
    if not query_families:
        return False

    hit_families = {
        family
        for hit in signals.negative_role_hits
        if (family := _NEGATIVE_HIT_FAMILIES.get(hit.code)) is not None
    }
    if not hit_families:
        return False

    return any(
        families_are_compatible(query_family, hit_family)
        for query_family in query_families
        for hit_family in hit_families
    )


def _check_profession_family_mismatch(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> bool:
    """True when the vacancy clearly conflicts with the profile's desired role families.

    First classifies the normalized job title. When the title is GENERIC (helfer,
    mitarbeiter, etc.), falls back to a conservative body-text check using only
    the most distinctive tokens to avoid false positives from incidental mentions.
    Returns False conservatively when both title and body are GENERIC.
    """
    if not profile.desired_roles:
        return False

    query_families = classify_desired_roles(profile.desired_roles)
    specific_query_families = {f for f in query_families if is_specific_family(f)}
    if not specific_query_families:
        return False

    normalized_title = normalize_text_for_fingerprint(canonical.normalized_title)
    vacancy_family = classify_vacancy_de(normalized_title)

    if not is_specific_family(vacancy_family):
        # Title is generic — inspect body text for strong contrary family signals
        vacancy_family = _classify_body_family(canonical)
        if not is_specific_family(vacancy_family):
            return False  # both title and body are generic → conservative pass

    return not any(families_are_compatible(qf, vacancy_family) for qf in specific_query_families)


def _is_non_it_writing_title_for_it_profile(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> bool:
    """Reject writing/content titles for IT profiles before noisy body terms can rescue them."""
    query_families = classify_desired_roles(profile.desired_roles)
    specific_query_families = {f for f in query_families if is_specific_family(f)}
    if RoleFamily.IT not in specific_query_families:
        return False

    normalized_title = normalize_text_for_fingerprint(canonical.normalized_title or "")
    if not normalized_title:
        return False
    if classify_vacancy_de(normalized_title) is RoleFamily.IT:
        return False

    if any(phrase in normalized_title for phrase in _NON_IT_WRITING_TITLE_PHRASES):
        return True
    title_tokens = set(normalized_title.split())
    return bool(title_tokens & _NON_IT_WRITING_TITLE_TOKENS)


def _classify_body_family(canonical: CanonicalVacancyGroup) -> RoleFamily:
    """Return a specific RoleFamily when body text contains unmistakable signals; GENERIC otherwise."""
    for record in canonical.source_records:
        body = record.normalized_body_text
        if not body:
            continue
        for family, tokens in _STRONG_BODY_TOKENS:
            if any(token in body for token in tokens):
                return family
    return RoleFamily.GENERIC


def _dedupe_hits(hits: list[RuleHit] | tuple[RuleHit, ...]) -> tuple[RuleHit, ...]:
    seen: set[str] = set()
    ordered: list[RuleHit] = []
    for hit in hits:
        if hit.code in seen:
            continue
        seen.add(hit.code)
        ordered.append(hit)
    return tuple(ordered)


def explain_filter_decision(canonical: CanonicalVacancyGroup, profile: SearchProfileContext) -> dict[str, object]:
    """Return a compact machine-readable explanation for one filter/scoring decision."""
    from app.services.rule_catalog import HOT_BUCKET_MIN_SCORE, MAYBE_BUCKET_MIN_SCORE, inspect_vacancy
    from app.services.scorer import VacancyScorer

    engine = FilterEngine()
    signals = inspect_vacancy(canonical, profile)
    filter_result = engine.evaluate(canonical, profile, signals=signals)
    score_result = VacancyScorer(filter_engine=engine).score(
        canonical,
        profile,
        signals=signals,
        filter_result=filter_result,
    )
    role_family = classify_vacancy_de(normalize_text_for_fingerprint(canonical.normalized_title or ""))

    if filter_result.hard_reject:
        final_decision = "hard_hidden"
    elif filter_result.review_required or score_result.score >= MAYBE_BUCKET_MIN_SCORE:
        final_decision = "hot" if score_result.score >= HOT_BUCKET_MIN_SCORE and not filter_result.review_required else "maybe"
    else:
        final_decision = "rejected"

    return {
        "final_decision": final_decision,
        "hard_reject": filter_result.hard_reject,
        "hard_reject_reason": tuple(hit.code for hit in filter_result.rejection_hits),
        "score": score_result.score,
        "matched_terms": tuple(hit.code for hit in (*signals.desired_role_hits, *score_result.positive_hits)),
        "negative_matches": tuple(hit.code for hit in (*signals.negative_role_hits, *score_result.negative_hits)),
        "language_reason": {
            "strong_german_required": signals.strong_german_required,
            "german_any_required": signals.german_any_required,
            "low_language_signal": signals.low_language_signal,
            "english_required_signal": signals.english_required_signal,
            "english_preferred_signal": signals.english_preferred_signal,
        },
        "role_family_reason": {
            "detected_role_family": role_family.value,
            "positive_role_hits": tuple(hit.code for hit in signals.positive_role_hits),
            "desired_role_hits": tuple(hit.code for hit in signals.desired_role_hits),
            "excluded_role_hits": tuple(hit.code for hit in signals.excluded_role_hits),
            "review_hits": tuple(hit.code for hit in filter_result.review_hits),
        },
    }
