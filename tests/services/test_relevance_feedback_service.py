from __future__ import annotations

import pytest

from app.services.relevance_feedback_service import RelevanceFeedbackService

import app.db.models  # noqa: F401 — ensures RelevanceFeedback is registered with Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc() -> RelevanceFeedbackService:
    return RelevanceFeedbackService()


def _record_relevant(db, svc, *, profile_id: int = 1, canonical_key: str = "ckey-1") -> None:
    svc.record_feedback(
        db,
        profile_id=profile_id,
        canonical_key=canonical_key,
        source_name="BA",
        normalized_title="Lagermitarbeiter",
        feedback_label="relevant",
    )


# ---------------------------------------------------------------------------
# Basic capture
# ---------------------------------------------------------------------------


def test_record_relevant(db_session) -> None:
    svc = _svc()
    rec = svc.record_feedback(
        db_session,
        profile_id=1,
        canonical_key="ckey-1",
        source_id="ba",
        source_name="BA",
        normalized_title="Lagermitarbeiter",
        company_name="Nord GmbH",
        location_text="Berlin",
        role_family="warehouse",
        current_query="lager",
        feedback_label="relevant",
    )
    assert rec.feedback_label == "relevant"
    assert rec.profile_id == 1
    assert rec.canonical_key == "ckey-1"
    assert rec.source_id == "ba"
    assert rec.source_name == "BA"
    assert rec.normalized_title == "Lagermitarbeiter"
    assert rec.company_name == "Nord GmbH"
    assert rec.location_text == "Berlin"
    assert rec.role_family == "warehouse"
    assert rec.current_query == "lager"
    assert rec.created_at is not None
    assert rec.updated_at is not None


def test_record_weak(db_session) -> None:
    svc = _svc()
    rec = svc.record_feedback(
        db_session,
        profile_id=1,
        canonical_key="ckey-weak",
        source_name="Careerjet",
        normalized_title="Fahrer",
        feedback_label="weak",
    )
    assert rec.feedback_label == "weak"


def test_record_irrelevant(db_session) -> None:
    svc = _svc()
    rec = svc.record_feedback(
        db_session,
        profile_id=1,
        canonical_key="ckey-irr",
        source_name="BA",
        normalized_title="Softwareentwickler",
        feedback_label="irrelevant",
    )
    assert rec.feedback_label == "irrelevant"


def test_invalid_label_raises(db_session) -> None:
    svc = _svc()
    with pytest.raises(ValueError, match="Invalid feedback_label"):
        svc.record_feedback(
            db_session,
            profile_id=1,
            canonical_key="ckey-bad",
            source_name="BA",
            normalized_title="Test",
            feedback_label="unknown_label",
        )


# ---------------------------------------------------------------------------
# Duplicate / upsert handling
# ---------------------------------------------------------------------------


def test_duplicate_feedback_upserts(db_session) -> None:
    svc = _svc()
    svc.record_feedback(
        db_session,
        profile_id=1,
        canonical_key="ckey-dup",
        source_id="ba",
        source_name="BA",
        normalized_title="Lagerhelfer",
        current_query="lager",
        feedback_label="relevant",
    )
    updated = svc.record_feedback(
        db_session,
        profile_id=1,
        canonical_key="ckey-dup",
        source_id="careerjet",
        source_name="BA",
        normalized_title="Lagerhelfer",
        current_query="produktion",
        feedback_label="irrelevant",
    )
    assert updated.feedback_label == "irrelevant"
    assert updated.source_id == "careerjet"
    assert updated.current_query == "produktion"
    # Only one row in DB
    all_fb = svc.list_profile_feedback(db_session, profile_id=1)
    assert len(all_fb) == 1
    assert all_fb[0].feedback_label == "irrelevant"


def test_same_canonical_different_profiles_are_independent(db_session) -> None:
    svc = _svc()
    svc.record_feedback(
        db_session, profile_id=1, canonical_key="ckey-shared",
        source_name="BA", normalized_title="Lager", feedback_label="relevant",
    )
    svc.record_feedback(
        db_session, profile_id=2, canonical_key="ckey-shared",
        source_name="BA", normalized_title="Lager", feedback_label="irrelevant",
    )
    p1 = svc.list_profile_feedback(db_session, profile_id=1)
    p2 = svc.list_profile_feedback(db_session, profile_id=2)
    assert p1[0].feedback_label == "relevant"
    assert p2[0].feedback_label == "irrelevant"


# ---------------------------------------------------------------------------
# get_feedback
# ---------------------------------------------------------------------------


def test_get_feedback_returns_record(db_session) -> None:
    svc = _svc()
    _record_relevant(db_session, svc, profile_id=1, canonical_key="ckey-get")
    rec = svc.get_feedback(db_session, profile_id=1, canonical_key="ckey-get")
    assert rec is not None
    assert rec.feedback_label == "relevant"


def test_get_feedback_returns_none_for_missing(db_session) -> None:
    svc = _svc()
    rec = svc.get_feedback(db_session, profile_id=1, canonical_key="no-such-key")
    assert rec is None


# ---------------------------------------------------------------------------
# list_profile_feedback
# ---------------------------------------------------------------------------


def test_list_profile_feedback_scoped_to_profile(db_session) -> None:
    svc = _svc()
    for i in range(3):
        svc.record_feedback(
            db_session, profile_id=1, canonical_key=f"ckey-{i}",
            source_name="BA", normalized_title=f"Job {i}", feedback_label="relevant",
        )
    svc.record_feedback(
        db_session, profile_id=99, canonical_key="ckey-other",
        source_name="BA", normalized_title="Other", feedback_label="irrelevant",
    )

    results = svc.list_profile_feedback(db_session, profile_id=1)
    assert len(results) == 3
    assert all(r.profile_id == 1 for r in results)


def test_list_profile_feedback_empty_when_no_feedback(db_session) -> None:
    svc = _svc()
    results = svc.list_profile_feedback(db_session, profile_id=42)
    assert results == []


def test_list_profile_feedback_respects_limit(db_session) -> None:
    svc = _svc()
    for i in range(10):
        svc.record_feedback(
            db_session, profile_id=1, canonical_key=f"ckey-limit-{i}",
            source_name="BA", normalized_title=f"Job {i}", feedback_label="relevant",
        )
    results = svc.list_profile_feedback(db_session, profile_id=1, limit=5)
    assert len(results) == 5


# ---------------------------------------------------------------------------
# delete_feedback
# ---------------------------------------------------------------------------


def test_delete_feedback_removes_record(db_session) -> None:
    svc = _svc()
    rec = svc.record_feedback(
        db_session, profile_id=1, canonical_key="ckey-del",
        source_name="BA", normalized_title="Lager", feedback_label="relevant",
    )
    deleted = svc.delete_feedback(db_session, feedback_id=rec.id, profile_id=1)
    assert deleted is True
    assert svc.get_feedback(db_session, profile_id=1, canonical_key="ckey-del") is None


def test_delete_feedback_wrong_profile_returns_false(db_session) -> None:
    svc = _svc()
    rec = svc.record_feedback(
        db_session, profile_id=1, canonical_key="ckey-del2",
        source_name="BA", normalized_title="Lager", feedback_label="relevant",
    )
    deleted = svc.delete_feedback(db_session, feedback_id=rec.id, profile_id=99)
    assert deleted is False
    assert svc.get_feedback(db_session, profile_id=1, canonical_key="ckey-del2") is not None
