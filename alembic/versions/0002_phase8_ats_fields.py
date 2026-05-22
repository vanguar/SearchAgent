"""phase8 ats crm fields

Revision ID: 0002_phase8_ats_fields
Revises: 0001_phase2_initial
Create Date: 2026-04-17 16:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_phase8_ats_fields"
down_revision = "0001_phase2_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_leads", sa.Column("source_id", sa.String(length=64), nullable=True))
    op.add_column("application_leads", sa.Column("source_external_id", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("canonical_key", sa.String(length=255), nullable=True))
    op.add_column(
        "application_leads",
        sa.Column("vacancy_title", sa.String(length=255), nullable=False, server_default=sa.text("''")),
    )
    op.add_column("application_leads", sa.Column("translated_title_ru", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("company_name", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("location_text", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("summary_ru", sa.Text(), nullable=True))
    op.add_column("application_leads", sa.Column("opened_original_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("application_leads", sa.Column("salary_expectation_text", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("contact_person", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("contact_email", sa.String(length=255), nullable=True))
    op.add_column("application_leads", sa.Column("follow_up_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("application_leads", sa.Column("follow_up_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_application_leads_source_identity",
        "application_leads",
        ["user_profile_id", "source_id", "source_external_id"],
    )
    op.create_index(
        "ix_application_leads_canonical_key",
        "application_leads",
        ["user_profile_id", "canonical_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_application_leads_canonical_key", table_name="application_leads")
    op.drop_index("ix_application_leads_source_identity", table_name="application_leads")
    op.drop_column("application_leads", "follow_up_sent_at")
    op.drop_column("application_leads", "follow_up_due_at")
    op.drop_column("application_leads", "contact_email")
    op.drop_column("application_leads", "contact_person")
    op.drop_column("application_leads", "salary_expectation_text")
    op.drop_column("application_leads", "opened_original_at")
    op.drop_column("application_leads", "summary_ru")
    op.drop_column("application_leads", "location_text")
    op.drop_column("application_leads", "company_name")
    op.drop_column("application_leads", "translated_title_ru")
    op.drop_column("application_leads", "vacancy_title")
    op.drop_column("application_leads", "canonical_key")
    op.drop_column("application_leads", "source_external_id")
    op.drop_column("application_leads", "source_id")
