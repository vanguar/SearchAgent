"""phase13 add search_query_de to search_profiles

Revision ID: 0005_phase13_search_query_de
Revises: 0004_phase11_default_profile
Create Date: 2026-04-19 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_phase13_search_query_de"
down_revision = "0004_phase11_default_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_profiles",
        sa.Column("search_query_de", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_profiles", "search_query_de")
