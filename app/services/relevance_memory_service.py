from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, cast

from sqlalchemy.orm import Session

from app.db.models.feedback import RelevanceFeedback

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
_ADJ_SOURCE_PENALTY = -3

# Hard bounds on total adjustment — deterministic rules always dominate
_ADJ_MAX = 8
_ADJ_MIN = -8

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
    total_feedback_count: int = 0

    @property
    def has_memory(self) -> bool:
        return self.total_feedback_count > 0


@dataclass(frozen=True)
class ScoreAdjustmentResult:
    adjustment: int       # bounded to [-8, +8]; 0 = no effect
    note_ru: str | None   # one-line Russian explainer; None if adjustment is 0


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
    ) -> ScoreAdjustmentResult:
        """Return bounded score adjustment and optional Russian explanation."""
        if not memory.has_memory:
            return ScoreAdjustmentResult(0, None)

        match_key = role_family or _title_key(normalized_title)

        pos_count = sum(
            p.count
            for p in memory.frequently_relevant
            if _keys_match(p.pattern_key, match_key)
        )
        neg_count = sum(
            p.count
            for p in memory.frequently_irrelevant
            if _keys_match(p.pattern_key, match_key)
        )
        weak_count = sum(
            p.count
            for p in memory.weak_tolerated
            if _keys_match(p.pattern_key, match_key)
        )

        source_penalty = 0
        for signal in memory.source_signals:
            if signal.source_name == source_name and signal.has_penalty:
                source_penalty = _ADJ_SOURCE_PENALTY
                break

        adjustment = 0

        if pos_count >= _STRONG:
            adjustment += _ADJ_STRONG
        elif pos_count >= _MODERATE:
            adjustment += _ADJ_MODERATE
        elif pos_count >= 1:
            adjustment += _ADJ_WEAK

        if neg_count >= _STRONG:
            adjustment -= _ADJ_STRONG
        elif neg_count >= _MODERATE:
            adjustment -= _ADJ_MODERATE
        elif neg_count >= 1:
            adjustment -= _ADJ_WEAK

        adjustment += source_penalty
        adjustment = max(_ADJ_MIN, min(_ADJ_MAX, adjustment))

        note_ru: str | None = None
        if adjustment > 0:
            note_ru = "Похоже на роли, которые вы уже отмечали как подходящие."
        elif adjustment < 0 and pos_count == 0 and source_penalty < 0:
            note_ru = "Этот источник часто показывал вам нерелевантные вакансии."
        elif adjustment < 0:
            note_ru = "Понижено, потому что похожие вакансии вы часто отмечали как нерелевантные."
        elif weak_count > 0:
            note_ru = "Смежная роль, которую вы ранее иногда принимали как допустимую."

        return ScoreAdjustmentResult(adjustment, note_ru)


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
