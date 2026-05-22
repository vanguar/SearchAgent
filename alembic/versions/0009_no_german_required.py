"""add no_german_required to search_profiles

Revision ID: 0009_no_german_required
Revises: 0008_phase162_feedback_context
Create Date: 2026-05-01 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_no_german_required"
down_revision = "0008_phase162_feedback_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_profiles",
        sa.Column("no_german_required", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("search_profiles", "no_german_required")
