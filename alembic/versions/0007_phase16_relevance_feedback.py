"""phase16 add relevance_feedback table

Revision ID: 0007_phase16_relevance_feedback
Revises: 0006_phase13_search_location_de
Create Date: 2026-04-21 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_phase16_relevance_feedback"
down_revision = "0006_phase13_search_location_de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relevance_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("canonical_key", sa.String(512), nullable=False),
        sa.Column("source_name", sa.String(120), nullable=False),
        sa.Column("normalized_title", sa.String(512), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("location_text", sa.String(255), nullable=True),
        sa.Column("role_family", sa.String(120), nullable=True),
        sa.Column("feedback_label", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["search_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id", "canonical_key",
            name="uq_relevance_feedback_profile_canonical",
        ),
    )
    op.create_index(
        "ix_relevance_feedback_profile_id",
        "relevance_feedback",
        ["profile_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_relevance_feedback_profile_id", table_name="relevance_feedback")
    op.drop_table("relevance_feedback")
