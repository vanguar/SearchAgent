from __future__ import annotations

import pytest
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.profile_catalog_service import ProfileCatalogService
from sqlalchemy.orm import Session


@pytest.fixture()
def user_profile(db_session: Session) -> UserProfile:
    up = UserProfile(display_name="Тест", work_authorized=True)
    db_session.add(up)
    db_session.commit()
    db_session.refresh(up)
    return up


@pytest.fixture()
def search_profile(db_session: Session, user_profile: UserProfile) -> SearchProfile:
    sp = SearchProfile(
        user_profile_id=user_profile.id,
        name="Склад / логистика",
        is_active=True,
        is_default=False,
        desired_roles=["lager", "logistik"],
    )
    db_session.add(sp)
    db_session.commit()
    db_session.refresh(sp)
    return sp


def test_list_profiles_empty(db_session: Session) -> None:
    service = ProfileCatalogService()
    result = service.list_profiles(db_session)
    assert result == []


def test_list_profiles_returns_entries(db_session: Session, search_profile: SearchProfile) -> None:
    service = ProfileCatalogService()
    result = service.list_profiles(db_session)
    assert len(result) == 1
    entry = result[0]
    assert entry.profile.name == "Склад / логистика"
    assert entry.is_default is False


def test_set_default(db_session: Session, search_profile: SearchProfile) -> None:
    service = ProfileCatalogService()
    ok = service.set_default(db_session, profile_id=search_profile.id)
    assert ok is True
    updated = db_session.get(SearchProfile, search_profile.id)
    assert updated.is_default is True


def test_set_default_clears_others(db_session: Session, user_profile: UserProfile) -> None:
    sp1 = SearchProfile(user_profile_id=user_profile.id, name="A", is_active=True, is_default=True)
    sp2 = SearchProfile(user_profile_id=user_profile.id, name="B", is_active=True, is_default=False)
    db_session.add_all([sp1, sp2])
    db_session.commit()

    service = ProfileCatalogService()
    service.set_default(db_session, profile_id=sp2.id)

    db_session.expire_all()
    assert db_session.get(SearchProfile, sp1.id).is_default is False
    assert db_session.get(SearchProfile, sp2.id).is_default is True


def test_set_default_nonexistent_returns_false(db_session: Session) -> None:
    service = ProfileCatalogService()
    result = service.set_default(db_session, profile_id=99999)
    assert result is False


def test_duplicate_profile(db_session: Session, search_profile: SearchProfile) -> None:
    service = ProfileCatalogService()
    copy = service.duplicate_profile(db_session, source_id=search_profile.id)
    assert copy is not None
    assert copy.id != search_profile.id
    assert "копия" in copy.name
    assert copy.is_default is False
    assert copy.desired_roles == search_profile.desired_roles


def test_duplicate_nonexistent_returns_none(db_session: Session) -> None:
    service = ProfileCatalogService()
    result = service.duplicate_profile(db_session, source_id=99999)
    assert result is None


def test_update_profile_name(db_session: Session, search_profile: SearchProfile) -> None:
    service = ProfileCatalogService()
    updated = service.update_profile(db_session, profile_id=search_profile.id, name="Новое название")
    assert updated is not None
    assert updated.name == "Новое название"


def test_update_profile_roles(db_session: Session, search_profile: SearchProfile) -> None:
    service = ProfileCatalogService()
    updated = service.update_profile(
        db_session, profile_id=search_profile.id, desired_roles_text="produktion, verpacker"
    )
    assert updated is not None
    assert "produktion" in updated.desired_roles
    assert "verpacker" in updated.desired_roles


def test_backfill_clears_location_for_remote_worldwide_profile(
    db_session: Session, user_profile: UserProfile
) -> None:
    sp = SearchProfile(
        user_profile_id=user_profile.id,
        name="Remote Python",
        is_active=True,
        desired_roles=["Python Developer"],
        preferred_locations=["worldwide remote", "Germany", "EU", "USA"],
        relocation_ready=False,
        search_location_de="Deutschland",
    )
    db_session.add(sp)
    db_session.commit()

    updated_count = ProfileCatalogService().backfill_search_fields(db_session)

    db_session.refresh(sp)
    assert updated_count == 1
    assert sp.search_location_de == "remote"


def test_backfill_keeps_deutschland_for_regular_profile(
    db_session: Session, user_profile: UserProfile
) -> None:
    sp = SearchProfile(
        user_profile_id=user_profile.id,
        name="Warehouse",
        is_active=True,
        desired_roles=["Склад"],
        preferred_locations=["Deutschland"],
        relocation_ready=True,
    )
    db_session.add(sp)
    db_session.commit()

    ProfileCatalogService().backfill_search_fields(db_session)

    db_session.refresh(sp)
    assert sp.search_location_de == "Deutschland"


def test_backfill_adds_remote_worldwide_markers_from_raw_profile_text(
    db_session: Session,
) -> None:
    up = UserProfile(
        display_name="Remote",
        work_authorized=True,
        raw_profile_text="Ищу только удаленную работу по всему миру, international remote companies.",
    )
    db_session.add(up)
    db_session.flush()
    sp = SearchProfile(
        user_profile_id=up.id,
        name="Remote Python",
        is_active=True,
        desired_roles=["Python Developer"],
        preferred_locations=["Deutschland", "EU", "USA"],
        relocation_ready=False,
        search_location_de="Deutschland",
    )
    db_session.add(sp)
    db_session.commit()

    ProfileCatalogService().backfill_search_fields(db_session)

    db_session.refresh(sp)
    assert "worldwide remote" in sp.preferred_locations
    assert "international remote companies" in sp.preferred_locations
    assert sp.search_location_de == "remote"


def test_resolve_default_profile_id_returns_default(db_session: Session, user_profile: UserProfile) -> None:
    sp1 = SearchProfile(user_profile_id=user_profile.id, name="A", is_active=True, is_default=False)
    sp2 = SearchProfile(user_profile_id=user_profile.id, name="B", is_active=True, is_default=True)
    db_session.add_all([sp1, sp2])
    db_session.commit()

    service = ProfileCatalogService()
    result = service.resolve_default_profile_id(db_session)
    assert result == sp2.id


def test_resolve_default_profile_id_falls_back_to_first_active(
    db_session: Session, user_profile: UserProfile
) -> None:
    sp = SearchProfile(user_profile_id=user_profile.id, name="A", is_active=True, is_default=False)
    db_session.add(sp)
    db_session.commit()

    service = ProfileCatalogService()
    result = service.resolve_default_profile_id(db_session)
    assert result == sp.id


def test_resolve_default_profile_id_no_profiles(db_session: Session) -> None:
    service = ProfileCatalogService()
    result = service.resolve_default_profile_id(db_session)
    assert result is None
