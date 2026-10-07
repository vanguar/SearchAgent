from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from sqlalchemy.orm import Session

from app.core.relevance_config import get_relevance_config
from app.db.models.feedback import RelevanceFeedback
from app.services.geo_distance import is_known_place
from app.services.hashers import normalize_text_for_fingerprint

FeedbackLabel = Literal["relevant", "weak", "irrelevant"]

_VALID_LABELS: frozenset[str] = frozenset({"relevant", "weak", "irrelevant"})
_TOKEN_RE = re.compile(r"[a-zA-Zа-яёА-ЯЁäöüÄÖÜß]{3,}")

# Pattern match thresholds
_STRONG = 3
_MODERATE = 2

# Score adjustment magnitudes (bounded in compute_score_adjustment)
_ADJ_STRONG = 6
_ADJ_MODERATE = 4
_ADJ_WEAK = 2

# Source penalty: fires when source has >= N irrelevant AND >= R rate irrelevant
_SOURCE_MIN_IRRELEVANT = 3
_SOURCE_PENALTY_RATE = 0.6


@dataclass(frozen=True)
class FeedbackPattern:
    pattern_key: str   # role_family or significant title tokens
    label: FeedbackLabel
    count: int


@dataclass(frozen=True)
class SourceQualitySignal:
    source_name: str
    relevant_count: int
    irrelevant_count: int
    weak_count: int
    has_penalty: bool


@dataclass
class ProfileFeedbackMemory:
    profile_id: int
    explicit_feedback_by_canonical_key: dict[str, FeedbackLabel] = field(default_factory=dict)
    frequently_relevant: list[FeedbackPattern] = field(default_factory=list)
    frequently_irrelevant: list[FeedbackPattern] = field(default_factory=list)
    weak_tolerated: list[FeedbackPattern] = field(default_factory=list)
    source_signals: list[SourceQualitySignal] = field(default_factory=list)
    # Заголовки вакансий, отмеченных нерелевантными: токены и семейство роли.
    # По ним считается похожесть конкретной вакансии, а не всего семейства.
    irrelevant_titles: list[tuple[frozenset[str], str | None]] = field(default_factory=list)
    total_feedback_count: int = 0

    @property
    def has_memory(self) -> bool:
        return self.total_feedback_count > 0


@dataclass(frozen=True)
class ScoreAdjustmentResult:
    adjustment: int       # bounded total; 0 = no effect (includes source_adjustment)
    note_ru: str | None   # one-line Russian explainer; None if adjustment is 0
    # Отдельная маленькая поправка за источник (уже входит в adjustment).
    source_adjustment: int = 0
    # Штраф был бы, но у вакансии сильные позитивные сигналы.
    penalty_suppressed: bool = False


class RelevanceMemoryService:
    """Aggregate per-profile feedback into bounded, explainable score adjustments.

    Design contract:
    - Hard deterministic rules (filter_engine) are applied before this layer.
    - This layer only shifts score within the already-allowed candidate space.
    - Maximum shift is ±8 points — cannot cross bucket thresholds on its own.
    - Hard reject cap in scorer still applies after this layer.
    """

    def build_profile_memory(
        self, db: Session, *, profile_id: int
    ) -> ProfileFeedbackMemory:
        rows = db.query(RelevanceFeedback).filter_by(profile_id=profile_id).all()
        return self.build_memory_from_rows(rows, profile_id=profile_id)

    def build_memory_from_rows(self, rows: Sequence[Any], *, profile_id: int) -> ProfileFeedbackMemory:
        """То же, что build_profile_memory, но по готовым строкам (без сессии БД).

        Строке достаточно атрибутов canonical_key, source_name, normalized_title,
        role_family и feedback_label.
        """
        memory = ProfileFeedbackMemory(
            profile_id=profile_id, total_feedback_count=len(rows)
        )
        if not rows:
            return memory

        family_counts: dict[str, dict[str, int]] = {}
        source_counts: dict[str, dict[str, int]] = {}

        for row in rows:
            label = _as_feedback_label(row.feedback_label)
            if label is None:
                continue
            memory.explicit_feedback_by_canonical_key[row.canonical_key] = label
            if label == "irrelevant":
                tokens = title_tokens(row.normalized_title or "")
                if tokens:
                    memory.irrelevant_titles.append((tokens, row.role_family))
            key = row.role_family or _title_key(row.normalized_title)
            if key:
                bucket = family_counts.setdefault(
                    key, {"relevant": 0, "weak": 0, "irrelevant": 0}
                )
                bucket[label] += 1

            sc = source_counts.setdefault(
                row.source_name, {"relevant": 0, "weak": 0, "irrelevant": 0}
            )
            sc[label] += 1

        for key, counts in family_counts.items():
            if counts["relevant"] >= 2:
                memory.frequently_relevant.append(
                    FeedbackPattern(key, "relevant", counts["relevant"])
                )
            if counts["irrelevant"] >= 2:
                memory.frequently_irrelevant.append(
                    FeedbackPattern(key, "irrelevant", counts["irrelevant"])
                )
            if counts["weak"] >= 1 and counts["irrelevant"] == 0:
                memory.weak_tolerated.append(
                    FeedbackPattern(key, "weak", counts["weak"])
                )

        for src, counts in source_counts.items():
            total = sum(counts.values())
            has_penalty = (
                total > 0
                and counts["irrelevant"] >= _SOURCE_MIN_IRRELEVANT
                and counts["irrelevant"] / total >= _SOURCE_PENALTY_RATE
            )
            memory.source_signals.append(
                SourceQualitySignal(
                    source_name=src,
                    relevant_count=counts["relevant"],
                    irrelevant_count=counts["irrelevant"],
                    weak_count=counts["weak"],
                    has_penalty=has_penalty,
                )
            )

        return memory

    def compute_score_adjustment(
        self,
        memory: ProfileFeedbackMemory,
        *,
        normalized_title: str,
        role_family: str | None,
        source_name: str,
        target_families: frozenset[str] | None = None,
        strong_positive: bool = False,
    ) -> ScoreAdjustmentResult:
        """Ограниченная поправка за обратную связь и пояснение к ней.

        Штраф считается по похожести ЗАГОЛОВКА на отмеченные нерелевантными.
        Похожесть по семейству роли используется только для семейств, которые
        профиль не ищет: если курьер отметил «LKW Fahrer» нерелевантным, это
        не повод понижать всю доставку, а IT-вакансии у складского профиля —
        повод. `target_families=None` — профиль неизвестен, семейство решает
        как раньше.

        `strong_positive` (подтверждённая ставка, полный день, категория B —
        хотя бы два из трёх) отменяет штрафы: такая вакансия заслуживает
        внимания, даже если похожие отмечались нерелевантными.
        """
        if not memory.has_memory:
            return ScoreAdjustmentResult(0, None)

        config = get_relevance_config()
        match_key = role_family or _title_key(normalized_title)
        family_is_target = bool(target_families) and role_family in (target_families or frozenset())

        pos_count = sum(
            p.count
            for p in memory.frequently_relevant
            if _keys_match(p.pattern_key, match_key)
        )
        family_neg_count = 0 if family_is_target else sum(
            p.count
            for p in memory.frequently_irrelevant
            if _keys_match(p.pattern_key, match_key)
        )
        vacancy_tokens = title_tokens(normalized_title)
        title_neg_count = sum(
            1
            for tokens, family in memory.irrelevant_titles
            if (family is None or role_family is None or family == role_family)
            and _jaccard(tokens, vacancy_tokens) >= config.feedback_title_similarity
        )
        neg_count = max(family_neg_count, title_neg_count)
        weak_count = sum(
            p.count
            for p in memory.weak_tolerated
            if _keys_match(p.pattern_key, match_key)
        )

        source_penalty = 0
        for signal in memory.source_signals:
            if signal.source_name == source_name and signal.has_penalty:
                source_penalty = -abs(config.feedback_source_penalty)
                break

        boost = _ADJ_STRONG if pos_count >= _STRONG else _ADJ_MODERATE if pos_count >= _MODERATE else (
            _ADJ_WEAK if pos_count >= 1 else 0
        )
        penalty = _ADJ_STRONG if neg_count >= _STRONG else _ADJ_MODERATE if neg_count >= _MODERATE else (
            _ADJ_WEAK if neg_count >= 1 else 0
        )
        suppressed = strong_positive and (penalty > 0 or source_penalty < 0)
        if suppressed:
            penalty = 0
            source_penalty = 0

        adjustment = boost - penalty + source_penalty
        adjustment = max(-abs(config.feedback_max_penalty), min(config.feedback_max_bonus, adjustment))

        note_ru: str | None = None
        if adjustment > 0:
            note_ru = "Похоже на роли, которые вы уже отмечали как подходящие."
        elif adjustment < 0 and penalty == 0 and source_penalty < 0:
            note_ru = "Этот источник часто показывал вам нерелевантные вакансии."
        elif adjustment < 0:
            note_ru = "Понижено: вакансии с похожим заголовком вы отмечали как нерелевантные."
        elif suppressed:
            note_ru = "Похожие вакансии вы отмечали нерелевантными, но у этой подтверждены ставка, полный день или категория B."
        elif weak_count > 0:
            note_ru = "Смежная роль, которую вы ранее иногда принимали как допустимую."

        return ScoreAdjustmentResult(
            adjustment,
            note_ru,
            source_adjustment=source_penalty,
            penalty_suppressed=suppressed,
        )


_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
# Слова, которые ничего не говорят о самой работе.
_TITLE_STOPWORDS = frozenset({
    "und", "oder", "fur", "mit", "der", "die", "das", "den", "dem", "des", "von", "auf", "bei", "ab",
    "vollzeit", "teilzeit", "minijob",
})


def title_tokens(title: str) -> frozenset[str]:
    """Значимые слова заголовка для сравнения с отмеченными вакансиями."""
    normalized = normalize_text_for_fingerprint(title)
    return frozenset(
        token for token in _TITLE_TOKEN_RE.findall(normalized)
        if token not in _TITLE_STOPWORDS and not token.isdigit() and not is_known_place(token)
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _title_key(title: str) -> str:
    """Reduce title to up to 3 most significant tokens for fuzzy matching."""
    tokens = _TOKEN_RE.findall(title.lower())
    significant = [t for t in tokens if len(t) >= 4][:3]
    return " ".join(significant)


def _as_feedback_label(value: str) -> FeedbackLabel | None:
    if value not in _VALID_LABELS:
        return None
    return cast(FeedbackLabel, value)


def _keys_match(pattern_key: str, match_key: str | None) -> bool:
    """True if keys are equal or share 2+ tokens (simple token-overlap similarity)."""
    if not match_key or not pattern_key:
        return False
    if pattern_key == match_key:
        return True
    p_tokens = set(pattern_key.lower().split())
    m_tokens = set(match_key.lower().split())
    return len(p_tokens & m_tokens) >= 2
