"""add transport_modes to search_profiles

На чём человек готов работать: car | van | bike | foot. NULL — не указано,
тогда вакансии по способу передвижения не фильтруются.

Revision ID: 0012_profile_transport_modes
Revises: 0011_profile_search_criteria
Create Date: 2026-10-07 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_profile_transport_modes"
down_revision = "0011_profile_search_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("search_profiles", sa.Column("transport_modes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("search_profiles", "transport_modes")
