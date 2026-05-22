from app.db.base import Base
import app.db.models  # noqa: F401


EXPECTED_TABLES = {
    "user_profiles",
    "search_profiles",
    "vacancy_source_records",
    "vacancy_canonicals",
    "vacancy_scores",
    "search_runs",
    "application_leads",
    "lead_events",
    "manual_notes",
    "reminder_tasks",
    "interview_events",
    "resumes",
    "resume_versions",
    "cover_letter_templates",
    "email_threads",
    "email_messages",
    "user_action_audits",
}


def test_phase2_metadata_contains_all_expected_tables() -> None:
    table_names = set(Base.metadata.tables.keys())

    assert EXPECTED_TABLES.issubset(table_names)
