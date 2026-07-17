from __future__ import annotations

from app.services.filter_engine import FilterEngine
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.rule_catalog import BASE_SCORE, inspect_vacancy
from app.services.search_models import (
    FilterResult,
    RuleHit,
    ScoreResult,
    SearchProfileContext,
    VacancySignalSnapshot,
    normalize_profile_text,
)
from app.services.search_normalizer import is_remote_worldwide_location

_CORE_STACK_RULES: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("stack_python", "Python в стеке", 14, ("python",)),
    ("stack_fastapi", "FastAPI в стеке", 10, ("fastapi", "fast api")),
    ("stack_django", "Django в стеке", 9, ("django",)),
    ("stack_ai_llm", "AI/LLM в стеке", 14, (" ai ", " ki ", " llm", "openai", "machine learning", " ml ", "kunstliche intelligenz")),
    ("stack_api_rest", "API/REST backend", 6, (" api ", "apis", "rest", "schnittstellen")),
    ("stack_websocket", "WebSocket в стеке", 5, ("websocket", "web socket", "websockets")),
    ("stack_scraping_data", "scraping/data pipeline", 8, ("scraping", "data pipeline", "datenpipeline", "data extraction")),
    ("stack_automation", "automation", 7, ("automation", "automatisierung")),
    ("stack_mvp_tools", "MVP/internal tools", 6, (" mvp ", "startup", "internal tools", "interne tools")),
)
_NON_CORE_STACK_RULES: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("stack_java_only", "стек скорее Java без Python", -4, ("java",)),
    ("stack_dotnet_csharp_only", "стек скорее .NET/C# без Python", -5, (" net ", " dotnet", "csharp")),
    ("stack_php_only", "стек скорее PHP без Python", -5, (" php ", "symfony", "laravel")),
    ("stack_devops_only", "DevOps без Python/backend stack", -6, ("devops", "azure devops", "kubernetes", "terraform")),
    ("stack_head_manager", "руководящая роль вместо hands-on Python", -8, ("head of", "engineering manager", "team lead", "leiter")),
)
_CORE_STACK_BONUS_CAP = 34
_CORE_STACK_HOT_COMBO_BONUS = 8
_TITLE_SHAPE_PENALTIES: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("title_devops_only", "title выглядит как DevOps-only", -24, ("devops", "site reliability", "sre")),
    ("title_head_manager", "title выглядит как руководящая роль", -24, ("head of", "engineering manager", "manager", "leiter")),
)


def _search_terms_match_vacancy(combined_text: str, search_query_terms: tuple[str, ...]) -> bool:
    """Return True if any distinctive token from search_query_terms has a bidirectional
    substring match with any token in the vacancy text.

    Bidirectional: term_token ⊆ vacancy_token  OR  vacancy_token ⊆ term_token.
    Only tokens of length >= 5 are considered to avoid noise from short common words.
    """
    vacancy_tokens = frozenset(t for t in combined_text.split() if len(t) >= 5)
    if not vacancy_tokens:
        return False
    for term in search_query_terms:
        term_norm = normalize_profile_text(term)
        for tok in term_norm.split():
            if len(tok) < 5:
                continue
            if any(tok in vt or vt in tok for vt in vacancy_tokens):
                return True
    return False

# Hard bounds for feedback adjustment — deterministic rules always dominate
_FEEDBACK_ADJ_MAX = 8
_FEEDBACK_ADJ_MIN = -8


class VacancyScorer:
    """Transparent deterministic scorer for PHASE 7 results."""

    def __init__(self, *, filter_engine: FilterEngine | None = None) -> None:
        self.filter_engine = filter_engine or FilterEngine()

    def score(
        self,
        canonical: CanonicalVacancyGroup,
        profile: SearchProfileContext,
        *,
        signals: VacancySignalSnapshot | None = None,
        filter_result: FilterResult | None = None,
        feedback_adjustment: int | None = None,
        search_mode: str | None = None,
    ) -> ScoreResult:
        resolved_signals = signals or inspect_vacancy(canonical, profile)
        resolved_filter = filter_result or self.filter_engine.evaluate(
            canonical, profile, signals=resolved_signals, search_mode=search_mode
        )

        score = BASE_SCORE
        positive_hits: list[RuleHit] = []
        negative_hits: list[RuleHit] = []

        if resolved_signals.positive_role_hits:
            role_bonus = 26 + min(6, (len(resolved_signals.positive_role_hits) - 1) * 3)
            score += role_bonus
            positive_hits.append(RuleHit(code="priority_role", label_ru="целевая складская или производственная роль", weight=role_bonus))

        if resolved_signals.desired_role_hits:
            score += 10
            positive_hits.append(RuleHit(code="desired_role_match", label_ru="совпадает с профилем поиска", weight=10))
        elif (
            profile.search_query_terms
            and not resolved_signals.positive_role_hits
            and _search_terms_match_vacancy(resolved_signals.combined_text, profile.search_query_terms)
        ):
            score += 10
            positive_hits.append(RuleHit(code="search_term_match", label_ru="соответствует поисковому термину профиля", weight=10))

        core_stack_hits = _score_core_stack(resolved_signals.combined_text, profile)
        core_stack_bonus = min(_CORE_STACK_BONUS_CAP, sum(hit.weight for hit in core_stack_hits))
        if core_stack_bonus:
            score += core_stack_bonus
            positive_hits.extend(core_stack_hits)
            if (
                resolved_signals.desired_role_hits
                and resolved_signals.location_match is not False
                and not (resolved_signals.strong_german_required and profile.low_german)
            ):
                score += _CORE_STACK_HOT_COMBO_BONUS
                positive_hits.append(
                    RuleHit(
                        code="core_stack_hot_combo",
                        label_ru="целевой backend плюс профильный стек",
                        weight=_CORE_STACK_HOT_COMBO_BONUS,
                    )
                )
        else:
            non_core_hits = _score_non_core_stack(resolved_signals.combined_text)
            if non_core_hits:
                score += sum(hit.weight for hit in non_core_hits)
                negative_hits.extend(non_core_hits)

        if resolved_signals.low_language_signal:
            score += 12
            positive_hits.append(RuleHit(code="low_language_signal", label_ru="низкий языковой барьер", weight=12))

        if resolved_signals.shift_signal and profile.accepts_shifts:
            score += 8
            positive_hits.append(RuleHit(code="shift_signal", label_ru="есть смены", weight=8))
        elif resolved_signals.shift_signal and profile.shift_ok is False:
            score -= 10
            negative_hits.append(RuleHit(code="shift_mismatch", label_ru="роль завязана на смены", weight=-10))

        if resolved_signals.relocation_signal and profile.allows_relocation:
            score += 8
            positive_hits.append(RuleHit(code="relocation_signal", label_ru="есть помощь с переездом или жильем", weight=8))

        if resolved_signals.immediate_start_signal:
            score += 8
            positive_hits.append(RuleHit(code="immediate_start_signal", label_ru="можно быстро выйти", weight=8))

        if resolved_signals.entry_level_signal:
            score += 8
            positive_hits.append(RuleHit(code="entry_level_signal", label_ru="без жесткого упора на опыт", weight=8))

        # A worldwide-remote run deliberately ignores geography (mirrors FilterEngine):
        # never penalize a location "mismatch" the user explicitly opted out of.
        worldwide_search = (
            search_mode == "remote_worldwide"
            or is_remote_worldwide_location(profile.preferred_locations)
        )
        if resolved_signals.location_match:
            score += 6
            positive_hits.append(RuleHit(code="location_match", label_ru="локация совпадает с профилем", weight=6))
        elif (
            resolved_signals.location_match is False
            and profile.relocation_ready is False
            and not worldwide_search
        ):
            score -= 15
            negative_hits.append(RuleHit(code="location_mismatch", label_ru="локация не совпадает с профилем", weight=-15))

        if resolved_signals.strong_german_required and profile.low_german:
            score -= 35
            negative_hits.append(RuleHit(code="strong_german_mismatch", label_ru="явно требуют хороший немецкий", weight=-35))

        if resolved_signals.degree_required:
            penalty = -25 if profile.low_barrier_focus else -12
            score += penalty
            negative_hits.append(RuleHit(code="degree_requirement", label_ru="нужен обязательный профильный диплом", weight=penalty))

        if resolved_signals.vocational_training_required:
            penalty = -22 if profile.low_barrier_focus else -10
            score += penalty
            negative_hits.append(RuleHit(code="vocational_requirement", label_ru="нужен обязательный Ausbildung", weight=penalty))

        if resolved_signals.strong_experience_required and not resolved_signals.entry_level_signal:
            penalty = -18 if profile.low_barrier_focus else -10
            score += penalty
            negative_hits.append(RuleHit(code="experience_requirement", label_ru="просят заметный профильный опыт", weight=penalty))

        if (
            resolved_signals.negative_role_hits
            and not resolved_signals.positive_role_hits
            and not resolved_signals.desired_role_hits
        ):
            score -= 24
            negative_hits.append(RuleHit(code="negative_role_family", label_ru="роль вне целевого профиля", weight=-24))

        if resolved_signals.excluded_role_hits:
            score -= 32
            negative_hits.append(RuleHit(code="excluded_role", label_ru="роль исключена профилем", weight=-32))

        if resolved_signals.sponsorship_ambiguity:
            score -= 6
            negative_hits.append(RuleHit(code="sponsorship_review", label_ru="есть вопросы по допуску к работе", weight=-6))

        title_penalties = _score_title_shape_penalties(canonical.normalized_title)
        if title_penalties:
            score += sum(hit.weight for hit in title_penalties)
            negative_hits.extend(title_penalties)

        # Bounded feedback adjustment — applied before hard reject cap.
        # Cannot override hard reject: cap below still enforces the deterministic ceiling.
        if feedback_adjustment is not None:
            adj = max(_FEEDBACK_ADJ_MIN, min(_FEEDBACK_ADJ_MAX, feedback_adjustment))
            if adj > 0:
                score += adj
                positive_hits.append(
                    RuleHit(
                        code="feedback_boost",
                        label_ru="похожие вакансии вы отмечали как подходящие",
                        weight=adj,
                    )
                )
            elif adj < 0:
                score += adj
                negative_hits.append(
                    RuleHit(
                        code="feedback_penalty",
                        label_ru="похожие вакансии часто отмечались как нерелевантные",
                        weight=adj,
                    )
                )

        # Defensive hard-reject cap for internal/test callers that invoke score() directly
        # without pre-filtering. In the live search path, hard-rejected items are excluded
        # by _build_result_item before scoring — so this cap does not affect visible results.
        if resolved_filter.hard_reject:
            score = min(score, 35)

        return ScoreResult(
            score=max(0, min(100, score)),
            positive_hits=_dedupe_hits(positive_hits),
            negative_hits=_dedupe_hits(negative_hits),
        )


def _dedupe_hits(hits: list[RuleHit]) -> tuple[RuleHit, ...]:
    seen: set[str] = set()
    ordered: list[RuleHit] = []
    for hit in hits:
        if hit.code in seen:
            continue
        seen.add(hit.code)
        ordered.append(hit)
    return tuple(ordered)


def _score_core_stack(text: str, profile: SearchProfileContext) -> list[RuleHit]:
    if not _profile_is_it_stack_search(profile):
        return []
    return [
        RuleHit(code=code, label_ru=label, weight=weight)
        for code, label, weight, tokens in _CORE_STACK_RULES
        if any(token in f" {text} " for token in tokens)
    ]


def _score_non_core_stack(text: str) -> list[RuleHit]:
    padded = f" {text} "
    if "python" in padded:
        return []
    return [
        RuleHit(code=code, label_ru=label, weight=weight)
        for code, label, weight, tokens in _NON_CORE_STACK_RULES
        if any(token in padded for token in tokens)
    ]


def _profile_is_it_stack_search(profile: SearchProfileContext) -> bool:
    profile_text = normalize_profile_text(" ".join((*profile.desired_roles, *profile.search_query_terms)))
    return any(
        token in profile_text
        for token in (
            "python",
            "fastapi",
            "django",
            "backend",
            "developer",
            "entwickler",
            "openai",
            "llm",
            "automation",
            "api",
            "scraping",
            "mvp",
        )
    )


def _score_title_shape_penalties(normalized_title: str | None) -> list[RuleHit]:
    text = f" {normalize_profile_text(normalized_title)} "
    if not text.strip():
        return []
    return [
        RuleHit(code=code, label_ru=label, weight=weight)
        for code, label, weight, tokens in _TITLE_SHAPE_PENALTIES
        if any(token in text for token in tokens)
    ]
