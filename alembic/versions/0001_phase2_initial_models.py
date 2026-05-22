"""phase2 initial database models

Revision ID: 0001_phase2_initial
Revises:
Create Date: 2026-04-15 12:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_phase2_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("legal_status", sa.String(length=120), nullable=True),
        sa.Column("work_authorized", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("german_level", sa.String(length=32), nullable=True),
        sa.Column("english_level", sa.String(length=32), nullable=True),
        sa.Column("raw_profile_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("desired_roles", sa.JSON(), nullable=True),
        sa.Column("excluded_roles", sa.JSON(), nullable=True),
        sa.Column("preferred_locations", sa.JSON(), nullable=True),
        sa.Column("relocation_ready", sa.Boolean(), nullable=True),
        sa.Column("shift_ok", sa.Boolean(), nullable=True),
        sa.Column("physical_work_ok", sa.Boolean(), nullable=True),
        sa.Column("housing_needed", sa.Boolean(), nullable=True),
        sa.Column("start_availability_text", sa.String(length=255), nullable=True),
        sa.Column("driver_license", sa.String(length=32), nullable=True),
        sa.Column("car_available", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "vacancy_canonicals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_title", sa.String(length=255), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("location_text", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("salary_text", sa.String(length=255), nullable=True),
        sa.Column("employment_type", sa.String(length=80), nullable=True),
        sa.Column("work_model", sa.String(length=80), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("source_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "vacancy_source_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_id", sa.Integer(), sa.ForeignKey("vacancy_canonicals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_name", "external_id", name="uq_source_external_id"),
    )
    op.create_index("ix_vacancy_source_records_canonical_id", "vacancy_source_records", ["canonical_id"])
    op.create_index("ix_vacancy_source_records_last_seen_at", "vacancy_source_records", ["last_seen_at"])
    op.create_index("ix_vacancy_canonicals_last_seen_at", "vacancy_canonicals", ["last_seen_at"])

    op.create_table(
        "vacancy_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vacancy_canonical_id", sa.Integer(), sa.ForeignKey("vacancy_canonicals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("search_profile_id", sa.Integer(), sa.ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score_total", sa.Float(), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vacancy_scores_search_profile_id", "vacancy_scores", ["search_profile_id"])

    op.create_table(
        "search_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("search_profile_id", sa.Integer(), sa.ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("total_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_scored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_search_runs_search_profile_id", "search_runs", ["search_profile_id"])

    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resume_id", sa.Integer(), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_label", sa.String(length=120), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=True),
        sa.Column("storage_hint", sa.String(length=255), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "cover_letter_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("template_body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "application_leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("search_profile_id", sa.Integer(), sa.ForeignKey("search_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vacancy_source_record_id", sa.Integer(), sa.ForeignKey("vacancy_source_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vacancy_canonical_id", sa.Integer(), sa.ForeignKey("vacancy_canonicals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resume_version_id", sa.Integer(), sa.ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cover_letter_template_id", sa.Integer(), sa.ForeignKey("cover_letter_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=True),
        sa.Column("original_url", sa.String(length=2048), nullable=True),
        sa.Column("found_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interview_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_channel", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_application_leads_status", "application_leads", ["status"])
    op.create_index("ix_application_leads_next_action_at", "application_leads", ["next_action_at"])

    op.create_table(
        "lead_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_lead_events_lead_id", "lead_events", ["lead_id"])

    op.create_table(
        "manual_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("search_profile_id", sa.Integer(), sa.ForeignKey("search_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "reminder_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_reminder_tasks_due_at", "reminder_tasks", ["due_at"])

    op.create_table(
        "interview_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interview_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interviewer_name", sa.String(length=255), nullable=True),
        sa.Column("interview_format", sa.String(length=64), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_interview_events_interview_at", "interview_events", ["interview_at"])

    op.create_table(
        "email_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_thread_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("participant_emails", sa.JSON(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_profile_id", "external_thread_id", name="uq_user_external_thread"),
    )
    op.create_index("ix_email_threads_last_message_at", "email_threads", ["last_message_at"])

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=False),
        sa.Column("from_email", sa.String(length=255), nullable=True),
        sa.Column("to_emails", sa.JSON(), nullable=True),
        sa.Column("cc_emails", sa.JSON(), nullable=True),
        sa.Column("bcc_emails", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("is_incoming", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("thread_id", "external_message_id", name="uq_thread_external_message"),
    )
    op.create_index("ix_email_messages_reply_at", "email_messages", ["reply_at"])

    op.create_table(
        "user_action_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("application_leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_name", sa.String(length=120), nullable=False),
        sa.Column("action_target", sa.String(length=255), nullable=True),
        sa.Column("action_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_action_audits_created_at", "user_action_audits", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_action_audits_created_at", table_name="user_action_audits")
    op.drop_table("user_action_audits")

    op.drop_index("ix_email_messages_reply_at", table_name="email_messages")
    op.drop_table("email_messages")

    op.drop_index("ix_email_threads_last_message_at", table_name="email_threads")
    op.drop_table("email_threads")

    op.drop_index("ix_interview_events_interview_at", table_name="interview_events")
    op.drop_table("interview_events")

    op.drop_index("ix_reminder_tasks_due_at", table_name="reminder_tasks")
    op.drop_table("reminder_tasks")

    op.drop_table("manual_notes")

    op.drop_index("ix_lead_events_lead_id", table_name="lead_events")
    op.drop_table("lead_events")

    op.drop_index("ix_application_leads_next_action_at", table_name="application_leads")
    op.drop_index("ix_application_leads_status", table_name="application_leads")
    op.drop_table("application_leads")

    op.drop_table("cover_letter_templates")
    op.drop_table("resume_versions")
    op.drop_table("resumes")

    op.drop_index("ix_search_runs_search_profile_id", table_name="search_runs")
    op.drop_table("search_runs")

    op.drop_index("ix_vacancy_scores_search_profile_id", table_name="vacancy_scores")
    op.drop_table("vacancy_scores")

    op.drop_index("ix_vacancy_source_records_last_seen_at", table_name="vacancy_source_records")
    op.drop_index("ix_vacancy_source_records_canonical_id", table_name="vacancy_source_records")
    op.drop_table("vacancy_source_records")

    op.drop_index("ix_vacancy_canonicals_last_seen_at", table_name="vacancy_canonicals")
    op.drop_table("vacancy_canonicals")

    op.drop_table("search_profiles")
    op.drop_table("user_profiles")
