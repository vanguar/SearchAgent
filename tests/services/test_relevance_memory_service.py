from __future__ import annotations

import app.db.models  # noqa: F401 — registers RelevanceFeedback with Base
from app.services.relevance_feedback_service import RelevanceFeedbackService
from app.services.relevance_memory_service import (
    ProfileFeedbackMemory,
    RelevanceMemoryService,
    _keys_match,
    _title_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fb(db, *, profile_id: int, canonical_key: str, source_name: str,
        normalized_title: str, feedback_label: str, role_family: str | None = None) -> None:
    RelevanceFeedbackService().record_feedback(
        db,
        profile_id=profile_id,
        canonical_key=canonical_key,
        source_name=source_name,
        normalized_title=normalized_title,
        role_family=role_family,
        feedback_label=feedback_label,
    )


def _svc() -> RelevanceMemoryService:
    return RelevanceMemoryService()


# ---------------------------------------------------------------------------
# Empty memory
# ---------------------------------------------------------------------------


def test_empty_memory_has_no_memory(db_session) -> None:
    memory = _svc().build_profile_memory(db_session, profile_id=999)
    assert not memory.has_memory
    assert memory.total_feedback_count == 0
    assert memory.explicit_feedback_by_canonical_key == {}
    assert memory.frequently_relevant == []
    assert memory.frequently_irrelevant == []
    assert memory.weak_tolerated == []
    assert memory.source_signals == []


def test_adjustment_zero_for_empty_memory() -> None:
    memory = ProfileFeedbackMemory(profile_id=1)
    result = _svc().compute_score_adjustment(
        memory,
        normalized_title="Lager",
        role_family=None,
        source_name="BA",
    )
    assert result.adjustment == 0
    assert result.note_ru is None


# ---------------------------------------------------------------------------
# Pattern aggregation
# ---------------------------------------------------------------------------


def test_frequently_relevant_requires_two_or_more(db_session) -> None:
    # Only 1 relevant → not frequent
    _fb(db_session, profile_id=1, canonical_key="k1", source_name="BA",
        normalized_title="Lager", feedback_label="relevant", role_family="warehouse")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    assert memory.frequently_relevant == []


def test_memory_keeps_explicit_feedback_by_canonical_key(db_session) -> None:
    _fb(db_session, profile_id=1, canonical_key="ckey-driver", source_name="Adzuna",
        normalized_title="Verkaufsfahrer", feedback_label="irrelevant", role_family="driving")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    assert memory.explicit_feedback_by_canonical_key == {"ckey-driver": "irrelevant"}


def test_frequently_relevant_with_two_same_family(db_session) -> None:
    _fb(db_session, profile_id=1, canonical_key="k1", source_name="BA",
        normalized_title="Lager 1", feedback_label="relevant", role_family="warehouse")
    _fb(db_session, profile_id=1, canonical_key="k2", source_name="BA",
        normalized_title="Lager 2", feedback_label="relevant", role_family="warehouse")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    assert len(memory.frequently_relevant) == 1
    assert memory.frequently_relevant[0].pattern_key == "warehouse"
    assert memory.frequently_relevant[0].count == 2


def test_frequently_irrelevant_with_two_same_family(db_session) -> None:
    _fb(db_session, profile_id=1, canonical_key="k1", source_name="BA",
        normalized_title="Software Dev", feedback_label="irrelevant", role_family="it")
    _fb(db_session, profile_id=1, canonical_key="k2", source_name="BA",
        normalized_title="Software Eng", feedback_label="irrelevant", role_family="it")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    assert len(memory.frequently_irrelevant) == 1
    assert memory.frequently_irrelevant[0].pattern_key == "it"


def test_weak_tolerated_requires_no_irrelevant(db_session) -> None:
    _fb(db_session, profile_id=1, canonical_key="k1", source_name="BA",
        normalized_title="Fahrer", feedback_label="weak", role_family="delivery")
    _fb(db_session, profile_id=1, canonical_key="k2", source_name="BA",
        normalized_title="Kurier", feedback_label="irrelevant", role_family="delivery")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    # Has irrelevant → NOT added to weak_tolerated
    assert memory.weak_tolerated == []


def test_weak_tolerated_added_when_no_irrelevant(db_session) -> None:
    _fb(db_session, profile_id=1, canonical_key="k1", source_name="BA",
        normalized_title="Fahrer", feedback_label="weak", role_family="delivery")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    assert len(memory.weak_tolerated) == 1
    assert memory.weak_tolerated[0].pattern_key == "delivery"


# ---------------------------------------------------------------------------
# Score adjustment — positive
# ---------------------------------------------------------------------------


def test_adjustment_positive_for_frequently_relevant(db_session) -> None:
    for i in range(3):
        _fb(db_session, profile_id=1, canonical_key=f"k{i}", source_name="BA",
            normalized_title=f"Lager {i}", feedback_label="relevant", role_family="warehouse")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Lager", role_family="warehouse", source_name="BA"
    )
    assert result.adjustment > 0
    assert result.note_ru is not None
    assert "подходящ" in result.note_ru.lower()


def test_adjustment_negative_for_frequently_irrelevant(db_session) -> None:
    for i in range(3):
        _fb(db_session, profile_id=1, canonical_key=f"k{i}", source_name="BA",
            normalized_title=f"Software {i}", feedback_label="irrelevant", role_family="it")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Software Dev", role_family="it", source_name="BA"
    )
    assert result.adjustment < 0


# ---------------------------------------------------------------------------
# Bounded adjustment — never exceeds ±8
# ---------------------------------------------------------------------------


def test_adjustment_never_exceeds_max(db_session) -> None:
    # Create many relevant feedbacks
    for i in range(20):
        _fb(db_session, profile_id=1, canonical_key=f"k{i}", source_name="BA",
            normalized_title=f"Lager {i}", feedback_label="relevant", role_family="warehouse")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Lager", role_family="warehouse", source_name="BA"
    )
    assert result.adjustment <= 8


def test_adjustment_never_below_min(db_session) -> None:
    for i in range(20):
        _fb(db_session, profile_id=1, canonical_key=f"k{i}", source_name="BA",
            normalized_title=f"Software {i}", feedback_label="irrelevant", role_family="it")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Software Dev", role_family="it", source_name="BA"
    )
    assert result.adjustment >= -8


# ---------------------------------------------------------------------------
# Source penalty
# ---------------------------------------------------------------------------


def test_source_penalty_fires_when_mostly_irrelevant(db_session) -> None:
    # 3 irrelevant, 0 relevant → rate = 100% >= 60%
    for i in range(3):
        _fb(db_session, profile_id=1, canonical_key=f"k{i}", source_name="BadSource",
            normalized_title=f"Job {i}", feedback_label="irrelevant", role_family="random")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    # No role match, but source penalty should apply
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Unrelated job", role_family=None, source_name="BadSource"
    )
    assert result.adjustment < 0


def test_source_penalty_does_not_fire_for_good_source(db_session) -> None:
    # 2 irrelevant, 5 relevant → rate 28% < 60%
    for i in range(5):
        _fb(db_session, profile_id=1, canonical_key=f"rel-{i}", source_name="GoodSource",
            normalized_title=f"Lager {i}", feedback_label="relevant", role_family="warehouse")
    for i in range(2):
        _fb(db_session, profile_id=1, canonical_key=f"irr-{i}", source_name="GoodSource",
            normalized_title=f"Random {i}", feedback_label="irrelevant", role_family="random")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    source_signal = next(
        (s for s in memory.source_signals if s.source_name == "GoodSource"), None
    )
    assert source_signal is not None
    assert not source_signal.has_penalty


# ---------------------------------------------------------------------------
# Profile isolation
# ---------------------------------------------------------------------------


def test_memory_scoped_to_profile(db_session) -> None:
    _fb(db_session, profile_id=1, canonical_key="k1", source_name="BA",
        normalized_title="Lager", feedback_label="relevant", role_family="warehouse")
    _fb(db_session, profile_id=1, canonical_key="k2", source_name="BA",
        normalized_title="Lager 2", feedback_label="relevant", role_family="warehouse")
    # Profile 2: 2 irrelevant → threshold for frequently_irrelevant met
    _fb(db_session, profile_id=2, canonical_key="k3", source_name="BA",
        normalized_title="Other", feedback_label="irrelevant", role_family="it")
    _fb(db_session, profile_id=2, canonical_key="k4", source_name="BA",
        normalized_title="Other 2", feedback_label="irrelevant", role_family="it")

    mem1 = _svc().build_profile_memory(db_session, profile_id=1)
    mem2 = _svc().build_profile_memory(db_session, profile_id=2)

    assert mem1.total_feedback_count == 2
    assert mem2.total_feedback_count == 2
    assert mem1.frequently_relevant != []
    assert mem2.frequently_irrelevant != []


# ---------------------------------------------------------------------------
# Domain evaluation fixtures — regression coverage
# ---------------------------------------------------------------------------


def test_warehouse_logistics_profile_not_disrupted_by_feedback(db_session) -> None:
    """Warehouse relevant signals stay dominant; feedback only adds ≤8 pts."""
    for i in range(3):
        _fb(db_session, profile_id=1, canonical_key=f"wh-{i}", source_name="BA",
            normalized_title=f"Lagerhelfer {i}", feedback_label="relevant",
            role_family="warehouse")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Lagermitarbeiter", role_family="warehouse", source_name="BA"
    )
    assert 0 < result.adjustment <= 8


def test_delivery_courier_profile(db_session) -> None:
    """Repeated 'weak' feedback for delivery gives note but no irrelevant downgrade."""
    for i in range(2):
        _fb(db_session, profile_id=1, canonical_key=f"del-{i}", source_name="BA",
            normalized_title=f"Fahrer {i}", feedback_label="weak", role_family="delivery")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Kurier", role_family="delivery", source_name="BA"
    )
    # Weak tolerated pattern → note present, but adjustment could be 0 (no irrelevant)
    assert result.adjustment >= 0


def test_it_support_profile_irrelevant_pattern(db_session) -> None:
    """IT vacancies marked irrelevant by warehouse user stay penalised."""
    for i in range(2):
        _fb(db_session, profile_id=1, canonical_key=f"it-{i}", source_name="Careerjet",
            normalized_title=f"Softwareentwickler {i}", feedback_label="irrelevant",
            role_family="it")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    result = _svc().compute_score_adjustment(
        memory, normalized_title="IT Support", role_family="it", source_name="Careerjet"
    )
    assert result.adjustment < 0


def test_customer_service_domain(db_session) -> None:
    """Customer service → neither boost nor penalty if no prior feedback for this family."""
    _fb(db_session, profile_id=1, canonical_key="wh-1", source_name="BA",
        normalized_title="Lager", feedback_label="relevant", role_family="warehouse")
    _fb(db_session, profile_id=1, canonical_key="wh-2", source_name="BA",
        normalized_title="Lager 2", feedback_label="relevant", role_family="warehouse")
    memory = _svc().build_profile_memory(db_session, profile_id=1)
    # customer_service family: no prior feedback → 0 adjustment
    result = _svc().compute_score_adjustment(
        memory, normalized_title="Kundenberater", role_family="customer_service", source_name="BA"
    )
    assert result.adjustment == 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_title_key_extracts_tokens() -> None:
    assert _title_key("Lagermitarbeiter Berlin") == "lagermitarbeiter berlin"
    assert _title_key("IT Support Specialist") == "support specialist"
    # Short tokens filtered out
    assert _title_key("") == ""


def test_keys_match_exact() -> None:
    assert _keys_match("warehouse", "warehouse") is True


def test_keys_match_token_overlap() -> None:
    assert _keys_match("lager mitarbeiter berlin", "lager mitarbeiter hamburg") is True


def test_keys_match_no_overlap() -> None:
    assert _keys_match("lager mitarbeiter", "software developer") is False


def test_keys_match_empty() -> None:
    assert _keys_match("", "warehouse") is False
    assert _keys_match("warehouse", None) is False
