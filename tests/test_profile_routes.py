from __future__ import annotations

import pytest
from app.db.models.profiles import SearchProfile, UserProfile
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture()
def two_profiles(db_session: Session) -> dict[str, object]:
    up = UserProfile(
        display_name="Тестовый пользователь",
        country_code="DE",
        city="Hamburg",
        legal_status="Section 24",
        work_authorized=True,
        german_level="B1",
        english_level="A2",
        raw_profile_text="Тест.",
    )
    db_session.add(up)
    db_session.flush()

    sp_a = SearchProfile(
        user_profile_id=up.id,
        name="Профиль А",
        is_active=True,
        is_default=True,
    )
    sp_b = SearchProfile(
        user_profile_id=up.id,
        name="Профиль Б",
        is_active=True,
        is_default=False,
    )
    db_session.add_all([sp_a, sp_b])
    db_session.commit()
    return {"user_profile": up, "profile_a": sp_a, "profile_b": sp_b}


def test_set_default_redirects_to_profile_list(client: TestClient, two_profiles: dict) -> None:
    profile_b = two_profiles["profile_b"]

    response = client.post(f"/profile/{profile_b.id}/set-default", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_set_default_marks_target_and_clears_previous(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]
    profile_b = two_profiles["profile_b"]

    client.post(f"/profile/{profile_b.id}/set-default")

    db_session.refresh(profile_a)
    db_session.refresh(profile_b)

    assert profile_b.is_default is True
    assert profile_a.is_default is False


def test_set_default_only_one_default_after_swap(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]
    profile_b = two_profiles["profile_b"]

    # Swap A→B then back B→A
    client.post(f"/profile/{profile_b.id}/set-default")
    client.post(f"/profile/{profile_a.id}/set-default")

    db_session.refresh(profile_a)
    db_session.refresh(profile_b)

    assert profile_a.is_default is True
    assert profile_b.is_default is False


def test_duplicate_profile_redirects_to_profile_list(client: TestClient, two_profiles: dict) -> None:
    profile_a = two_profiles["profile_a"]

    response = client.post(f"/profile/{profile_a.id}/duplicate", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_duplicate_profile_creates_new_profile_in_db(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]

    client.post(f"/profile/{profile_a.id}/duplicate")

    all_profiles = db_session.query(SearchProfile).all()
    assert len(all_profiles) == 3  # original two + copy

    copy = next(p for p in all_profiles if "копия" in p.name)
    assert copy.is_default is False
    assert copy.user_profile_id == profile_a.user_profile_id


def test_duplicate_profile_does_not_set_copy_as_default(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]

    client.post(f"/profile/{profile_a.id}/duplicate")

    db_session.refresh(profile_a)
    defaults = db_session.query(SearchProfile).filter(SearchProfile.is_default.is_(True)).all()
    assert len(defaults) == 1
    assert defaults[0].id == profile_a.id


# ---------------------------------------------------------------------------
# Delete profile tests
# ---------------------------------------------------------------------------


def test_delete_profile_redirects_to_profile_list(client: TestClient, two_profiles: dict) -> None:
    profile_b = two_profiles["profile_b"]

    response = client.post(f"/profile/{profile_b.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_delete_profile_removes_from_db(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_b = two_profiles["profile_b"]
    profile_b_id = profile_b.id

    client.post(f"/profile/{profile_b_id}/delete")

    remaining = db_session.query(SearchProfile).all()
    assert len(remaining) == 1
    assert remaining[0].id != profile_b_id


def test_deleted_profile_not_shown_on_profile_page(
    client: TestClient, two_profiles: dict
) -> None:
    profile_b = two_profiles["profile_b"]

    client.post(f"/profile/{profile_b.id}/delete")

    response = client.get("/profile")
    assert response.status_code == 200
    assert "Профиль Б" not in response.text


def test_delete_default_profile_promotes_next(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]  # is_default=True
    profile_b = two_profiles["profile_b"]

    client.post(f"/profile/{profile_a.id}/delete")

    db_session.expire_all()
    remaining = db_session.query(SearchProfile).all()
    assert len(remaining) == 1
    assert remaining[0].id == profile_b.id
    assert remaining[0].is_default is True


def test_delete_non_default_profile_keeps_default(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]  # is_default=True
    profile_b = two_profiles["profile_b"]

    client.post(f"/profile/{profile_b.id}/delete")

    db_session.expire_all()
    db_session.refresh(profile_a)
    assert profile_a.is_default is True


def test_delete_last_profile_leaves_empty_state(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_a = two_profiles["profile_a"]
    profile_b = two_profiles["profile_b"]

    client.post(f"/profile/{profile_a.id}/delete")
    client.post(f"/profile/{profile_b.id}/delete")

    remaining = db_session.query(SearchProfile).all()
    assert len(remaining) == 0

    response = client.get("/profile")
    assert response.status_code == 200
    # Empty state still renders and keeps a path to create a profile (redesign v2).
    assert "Профилей пока нет" in response.text
    assert "/profile/intake" in response.text


def test_delete_nonexistent_profile_still_redirects(client: TestClient) -> None:
    response = client.post("/profile/99999/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_profile_page_contains_delete_button(client: TestClient, two_profiles: dict) -> None:
    response = client.get("/profile")
    assert response.status_code == 200
    assert "Удалить" in response.text
    assert "/delete" in response.text


def test_get_delete_does_not_delete_profile(
    client: TestClient, db_session: Session, two_profiles: dict
) -> None:
    profile_b = two_profiles["profile_b"]
    profile_b_id = profile_b.id

    response = client.get(f"/profile/{profile_b_id}/delete", follow_redirects=False)

    # FastAPI returns 405 for unregistered methods; profile must still exist
    assert response.status_code == 405
    assert db_session.get(type(profile_b), profile_b_id) is not None
