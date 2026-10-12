"""Add opt-in strict matching without changing existing profile settings."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_strict_match_mode"
down_revision = "0012_profile_transport_modes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("search_profiles", sa.Column(
        "strict_match_mode", sa.String(32), nullable=False, server_default="off",
    ))


def downgrade() -> None:
    op.drop_column("search_profiles", "strict_match_mode")
