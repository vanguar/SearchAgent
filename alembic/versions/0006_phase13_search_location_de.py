"""phase13 add search_location_de to search_profiles

Revision ID: 0006_phase13_search_location_de
Revises: 0005_phase13_search_query_de
Create Date: 2026-04-19 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_phase13_search_location_de"
down_revision = "0005_phase13_search_query_de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_profiles",
        sa.Column("search_location_de", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_profiles", "search_location_de")
