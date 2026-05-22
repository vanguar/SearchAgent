"""add search_query_terms to search_profiles

Revision ID: 0010_search_query_terms
Revises: 0009_no_german_required
Create Date: 2026-05-04 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_search_query_terms"
down_revision = "0009_no_german_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_profiles",
        sa.Column("search_query_terms", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_profiles", "search_query_terms")
