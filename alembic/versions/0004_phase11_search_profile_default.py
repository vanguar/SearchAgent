"""phase11 add is_default to search_profiles

Revision ID: 0004_phase11_default_profile
Revises: 0003_phase81_cleanup_dedup
Create Date: 2026-04-18 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_phase11_default_profile"
down_revision = "0003_phase81_cleanup_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_profiles",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("search_profiles", "is_default")
