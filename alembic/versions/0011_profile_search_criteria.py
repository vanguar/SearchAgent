"""add user-editable search criteria to search_profiles

Поля, которые пользователь задаёт сам через форму профиля: радиус поиска,
дополнительные ключевые слова, тип занятости, минимальная ставка, допустимость
самозанятости. Все — nullable: «не указано» остаётся отдельным, значащим
состоянием и не подменяется значением по умолчанию.

Revision ID: 0011_profile_search_criteria
Revises: 0010_search_query_terms
Create Date: 2026-09-29 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_profile_search_criteria"
down_revision = "0010_search_query_terms"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    # Радиус вокруг заказанных городов, км. NULL = «искать строго в городах».
    ("search_radius_km", sa.Integer()),
    # Ключевые слова, которые добавляются к плану ПОСЛЕ основных.
    ("additional_search_terms", sa.JSON()),
    # Допустимые формы занятости: full_time | part_time | mini_job | temporary.
    ("employment_types", sa.JSON()),
    # Ориентир по оплате в евро за час. Предпочтение для ранжирования, не фильтр.
    ("min_salary_eur_per_hour", sa.Float()),
    # Готов ли человек на самозанятость (Gewerbeschein / Subunternehmer).
    ("self_employment_ok", sa.Boolean()),
)


def upgrade() -> None:
    for name, column_type in _NEW_COLUMNS:
        op.add_column("search_profiles", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_NEW_COLUMNS):
        op.drop_column("search_profiles", name)
