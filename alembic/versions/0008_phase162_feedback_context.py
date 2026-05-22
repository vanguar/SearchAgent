"""phase16.2 add feedback context fields

Revision ID: 0008_phase162_feedback_context
Revises: 0007_phase16_relevance_feedback
Create Date: 2026-04-21 11:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_phase162_feedback_context"
down_revision = "0007_phase16_relevance_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "relevance_feedback",
        sa.Column("source_id", sa.String(120), nullable=True),
    )
    op.add_column(
        "relevance_feedback",
        sa.Column("current_query", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("relevance_feedback", "current_query")
    op.drop_column("relevance_feedback", "source_id")
