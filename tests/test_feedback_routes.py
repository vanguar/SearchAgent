from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401


# ---------------------------------------------------------------------------
# POST /feedback/record
# ---------------------------------------------------------------------------


def _feedback_payload(
    *,
    profile_id: int | None = 1,
    canonical_key: str = "ckey-test",
    source_name: str = "BA",
    normalized_title: str = "Lagermitarbeiter",
    feedback_label: str = "relevant",
) -> dict[str, str]:
    payload: dict[str, str] = {
        "canonical_key": canonical_key,
        "source_name": source_name,
        "normalized_title": normalized_title,
        "company_name": "Nord GmbH",
        "location_text": "Berlin",
        "role_family": "",
        "feedback_label": feedback_label,
    }
    if profile_id is not None:
        payload["profile_id"] = str(profile_id)
    return payload


def test_feedback_record_relevant(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    resp = client.post(
        "/feedback/record",
        data={**_feedback_payload(profile_id=profile_id, feedback_label="relevant")},
    )
    assert resp.status_code == 200
    assert "badge-success" in resp.text
    assert "Подходит" in resp.text


def test_feedback_record_weak(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    resp = client.post(
        "/feedback/record",
        data={**_feedback_payload(profile_id=profile_id, feedback_label="weak")},
    )
    assert resp.status_code == 200
    assert "Слабо" in resp.text


def test_feedback_record_irrelevant(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    resp = client.post(
        "/feedback/record",
        data={**_feedback_payload(profile_id=profile_id, feedback_label="irrelevant")},
    )
    assert resp.status_code == 200
    assert "Не подходит" in resp.text


def test_feedback_record_invalid_label_returns_error(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    resp = client.post(
        "/feedback/record",
        data={**_feedback_payload(profile_id=profile_id, feedback_label="super_relevant_lol")},
    )
    assert resp.status_code == 200
    assert "Ошибка" in resp.text


def test_feedback_record_no_profile_id_returns_notice(client: TestClient) -> None:
    resp = client.post(
        "/feedback/record",
        data={
            "canonical_key": "ckey-noprofile",
            "source_name": "BA",
            "normalized_title": "Job",
            "feedback_label": "relevant",
        },
    )
    assert resp.status_code == 200
    assert "Профиль не выбран" in resp.text


def test_feedback_record_missing_canonical_key_returns_error(
    client: TestClient, owner_records: dict
) -> None:
    profile_id = owner_records["search_profile"].id
    resp = client.post(
        "/feedback/record",
        data={
            "profile_id": str(profile_id),
            "source_name": "BA",
            "normalized_title": "Job",
            "feedback_label": "relevant",
            # canonical_key intentionally missing
        },
    )
    assert resp.status_code == 200
    assert "Ошибка" in resp.text


def test_feedback_record_idempotent_upsert(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    payload = _feedback_payload(
        profile_id=profile_id,
        canonical_key="ckey-upsert",
        feedback_label="relevant",
    )
    # First post
    r1 = client.post("/feedback/record", data=payload)
    assert r1.status_code == 200
    # Second post — same canonical_key, different label
    payload2 = {**payload, "feedback_label": "irrelevant"}
    r2 = client.post("/feedback/record", data=payload2)
    assert r2.status_code == 200
    assert "Не подходит" in r2.text


# ---------------------------------------------------------------------------
# GET /feedback/profile/{profile_id}
# ---------------------------------------------------------------------------


def test_feedback_profile_summary_no_feedback(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    resp = client.get(f"/feedback/profile/{profile_id}")
    assert resp.status_code == 200
    assert "Оценок пока нет" in resp.text


def test_feedback_profile_summary_with_feedback(client: TestClient, owner_records: dict) -> None:
    profile_id = owner_records["search_profile"].id
    # Record some feedback first
    client.post(
        "/feedback/record",
        data=_feedback_payload(
            profile_id=profile_id,
            canonical_key="ckey-summary-1",
            feedback_label="relevant",
        ),
    )
    resp = client.get(f"/feedback/profile/{profile_id}")
    assert resp.status_code == 200
    assert "Память профиля" in resp.text
    # Should show the feedback in recent list
    assert "Lagermitarbeiter" in resp.text


def test_feedback_profile_summary_invalid_profile_renders(client: TestClient) -> None:
    # Profile 99999 doesn't exist — should still render gracefully (no feedback)
    resp = client.get("/feedback/profile/99999")
    assert resp.status_code == 200
    assert "Оценок пока нет" in resp.text


# ---------------------------------------------------------------------------
# Feedback memory shown in summary
# ---------------------------------------------------------------------------


def test_feedback_summary_shows_frequently_relevant(
    client: TestClient, owner_records: dict
) -> None:
    profile_id = owner_records["search_profile"].id
    # Record 2 feedbacks for the same role_family — makes it "frequently relevant"
    for i in range(2):
        client.post(
            "/feedback/record",
            data={
                "profile_id": str(profile_id),
                "canonical_key": f"ckey-fr-{i}",
                "source_name": "BA",
                "normalized_title": f"Lagermitarbeiter {i}",
                "company_name": "",
                "location_text": "",
                "role_family": "warehouse",
                "feedback_label": "relevant",
            },
        )
    resp = client.get(f"/feedback/profile/{profile_id}")
    assert resp.status_code == 200
    assert "warehouse" in resp.text
    assert "Часто отмечали как подходящие" in resp.text
