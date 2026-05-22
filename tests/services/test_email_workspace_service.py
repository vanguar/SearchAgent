from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.models.profiles import SearchProfile, UserProfile
from app.services.email.workspace_service import EmailWorkspaceService


@pytest.fixture()
def owner_with_two_profiles(db_session: Session) -> dict[str, object]:
    up = UserProfile(
        display_name="Тест",
        country_code="DE",
        city="Berlin",
        legal_status="Section 24",
        work_authorized=True,
        german_level="A2",
        english_level="A1",
        raw_profile_text="Тест.",
    )
    db_session.add(up)
    db_session.flush()

    sp_default = SearchProfile(
        user_profile_id=up.id,
        name="Профиль по умолчанию",
        is_active=True,
        is_default=True,
    )
    sp_other = SearchProfile(
        user_profile_id=up.id,
        name="Второй профиль",
        is_active=True,
        is_default=False,
    )
    db_session.add_all([sp_default, sp_other])
    db_session.commit()
    return {"user_profile": up, "sp_default": sp_default, "sp_other": sp_other}


def test_build_workspace_with_explicit_profile_id_resolves_that_profile(
    db_session: Session, owner_with_two_profiles: dict
) -> None:
    sp_other = owner_with_two_profiles["sp_other"]
    service = EmailWorkspaceService()

    workspace = service.build_workspace(db_session, selected_thread_id=None, notice=None, profile_id=sp_other.id)

    assert workspace.owner_label == sp_other.name
    assert workspace.warning_message is None


def test_build_workspace_without_profile_id_falls_back_gracefully(
    db_session: Session, owner_with_two_profiles: dict
) -> None:
    sp_default = owner_with_two_profiles["sp_default"]
    service = EmailWorkspaceService()

    workspace = service.build_workspace(db_session, selected_thread_id=None, notice=None)

    assert workspace.owner_label == sp_default.name
    assert workspace.warning_message is None
    assert workspace.degraded is False
