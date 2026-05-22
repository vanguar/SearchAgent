from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_alembic_config(sqlite_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", sqlite_url)
    return config


def test_phase2_migration_upgrade_downgrade_upgrade_cycle(tmp_path: Path) -> None:
    db_path = tmp_path / "phase2_migration.sqlite3"
    sqlite_url = f"sqlite:///{db_path.as_posix()}"

    config = _make_alembic_config(sqlite_url)

    command.upgrade(config, "head")

    engine = create_engine(sqlite_url)
    inspector = inspect(engine)
    assert "user_profiles" in inspector.get_table_names()
    assert "vacancy_source_records" in inspector.get_table_names()

    command.downgrade(config, "base")

    inspector = inspect(engine)
    assert "user_profiles" not in inspector.get_table_names()

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "application_leads" in inspector.get_table_names()
    assert "email_messages" in inspector.get_table_names()
    lead_columns = {column["name"] for column in inspector.get_columns("application_leads")}
    assert {
        "source_id",
        "source_external_id",
        "canonical_key",
        "vacancy_title",
        "opened_original_at",
        "salary_expectation_text",
        "contact_person",
        "contact_email",
        "follow_up_due_at",
        "follow_up_sent_at",
    }.issubset(lead_columns)
    unique_constraints = inspector.get_unique_constraints("application_leads")
    assert any(
        constraint["name"] == "uq_application_leads_user_source_identity"
        and constraint["column_names"] == ["user_profile_id", "source_id", "source_external_id"]
        for constraint in unique_constraints
    )


def test_phase81_cleanup_migration_collapses_existing_duplicate_source_identity_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "phase81_duplicate_cleanup.sqlite3"
    sqlite_url = f"sqlite:///{db_path.as_posix()}"

    config = _make_alembic_config(sqlite_url)
    command.upgrade(config, "0002_phase8_ats_fields")

    engine = create_engine(sqlite_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_profiles (id, display_name, work_authorized, created_at, updated_at)
                VALUES (1, 'Основной профиль', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO application_leads (
                    id,
                    user_profile_id,
                    source_name,
                    original_url,
                    next_action_at,
                    application_channel,
                    status,
                    created_at,
                    updated_at,
                    source_id,
                    source_external_id,
                    vacancy_title
                )
                VALUES
                    (10, 1, 'BA', 'https://example.org/jobs/dup', NULL, NULL, 'saved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'ba', 'dup-123', 'Lead A'),
                    (11, 1, 'BA', 'https://example.org/jobs/dup', NULL, NULL, 'saved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'ba', 'dup-123', 'Lead B')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO lead_events (id, lead_id, event_type, event_at, created_at)
                VALUES (100, 11, 'saved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        remaining_leads = connection.execute(
            text(
                """
                SELECT id
                FROM application_leads
                WHERE user_profile_id = 1
                  AND source_id = 'ba'
                  AND source_external_id = 'dup-123'
                ORDER BY id ASC
                """
            )
        ).scalars().all()
        reassigned_lead_id = connection.execute(text("SELECT lead_id FROM lead_events WHERE id = 100")).scalar_one()

    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("application_leads")

    assert remaining_leads == [10]
    assert reassigned_lead_id == 10
    assert any(
        constraint["name"] == "uq_application_leads_user_source_identity"
        and constraint["column_names"] == ["user_profile_id", "source_id", "source_external_id"]
        for constraint in unique_constraints
    )
