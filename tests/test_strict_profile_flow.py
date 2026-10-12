import pytest
from app.db.models.profiles import SearchProfile
from app.services.profile_catalog_service import ProfileCatalogService
from app.services.profile_form_spec import parse_profile_form
from app.services.search_profile_resolver import DatabaseSearchProfileResolver
from sqlalchemy import select


def test_new_form_exposes_off_by_default_without_creating_any_profile(client, db_session):
    response = client.get("/profile/new")
    assert response.status_code == 200
    assert 'name="strict_match_mode" value="off" checked' in response.text
    assert 'value="no_german"' in response.text
    assert 'value="a1"' in response.text
    assert db_session.scalars(select(SearchProfile)).all() == []


def test_strict_form_roundtrip_isolated_from_existing_profile_and_shared_person(client, db_session, owner_records):
    existing = owner_records["search_profile"]
    person = owner_records["user_profile"]
    before = (existing.name, existing.desired_roles[:], person.german_level, person.legal_status)
    response = client.post("/profile/new", data={
        "name": "Новый строгий поиск", "primary_role": "Lagerarbeiter", "additional_roles": "Auslieferungsfahrer",
        "primary_city": "Berlin", "additional_cities": "Neustrelitz, Neubrandenburg, Rostock",
        "strict_match_mode": "a1", "search_radius_km": "0", "driver_license": "B",
    }, follow_redirects=False)
    assert response.status_code == 303
    new = db_session.scalar(select(SearchProfile).where(SearchProfile.id != existing.id))
    assert new.strict_match_mode == "a1"
    assert new.search_radius_km == 0
    assert not new.is_default
    assert new.driver_license == "B"
    assert len(new.preferred_locations) == 4
    assert (existing.name, existing.desired_roles, person.german_level, person.legal_status) == before
    assert existing.strict_match_mode == "off"
    assert 'name="strict_match_mode" value="a1" checked' in client.get(f"/profile/{new.id}/edit").text
    copy = ProfileCatalogService().duplicate_profile(db_session, source_id=new.id)
    assert copy.strict_match_mode == "a1"
    client.post(f"/profile/{new.id}/edit", data={"name": "Renamed"})
    db_session.refresh(new)
    assert new.strict_match_mode == "a1"
    client.post(f"/profile/{new.id}/edit", data={"strict_match_mode": "off"})
    db_session.refresh(new)
    assert new.strict_match_mode == "off"
    assert existing.strict_match_mode == "off"


def test_resolver_propagates_strict_mode(db_session, owner_records):
    saved = owner_records["search_profile"]
    saved.strict_match_mode = "no_german"
    db_session.commit()
    context = DatabaseSearchProfileResolver(session_factory=lambda: db_session).resolve(profile_id=saved.id)
    assert context.strict_match_mode == "no_german"
    assert context.section24_interpreted


@pytest.mark.parametrize("mode", ["", "true", "unknown", "A1"])
def test_invalid_mode_does_not_silently_disable_strict_matching(mode, client, db_session):
    with pytest.raises(ValueError):
        parse_profile_form({"strict_match_mode": mode})
    assert client.post("/profile/new", data={"strict_match_mode": mode}).status_code == 422
    assert db_session.scalars(select(SearchProfile)).all() == []


def test_upgrade_preserves_every_existing_profile_value_and_creates_no_profile(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    url = f"sqlite:///{(tmp_path / 'strict-upgrade.sqlite3').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0012_profile_transport_modes")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO user_profiles (id, display_name, work_authorized, created_at, updated_at) "
                          "VALUES (1, 'Person', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO search_profiles "
                          "(id, user_profile_id, name, is_active, is_default, no_german_required, driver_license, "
                          "created_at, updated_at) VALUES (1, 1, 'Existing', 1, 1, 1, 'B', "
                          "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        before = dict(conn.execute(text("SELECT * FROM search_profiles")).mappings().one())
    command.upgrade(config, "head")
    with engine.connect() as conn:
        after = dict(conn.execute(text("SELECT * FROM search_profiles")).mappings().one())
        assert after.pop("strict_match_mode") == "off"
        assert after == before
    command.downgrade(config, "0012_profile_transport_modes")
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT * FROM search_profiles")).mappings().one()) == before
    engine.dispose()
