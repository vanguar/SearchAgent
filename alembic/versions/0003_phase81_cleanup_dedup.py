"""phase8.1 cleanup dedup

Revision ID: 0003_phase81_cleanup_dedup
Revises: 0002_phase8_ats_fields
Create Date: 2026-04-17 18:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_phase81_cleanup_dedup"
down_revision = "0002_phase8_ats_fields"
branch_labels = None
depends_on = None

_LEAD_REFERENCE_TABLES: tuple[tuple[str, str], ...] = (
    ("lead_events", "lead_id"),
    ("manual_notes", "lead_id"),
    ("reminder_tasks", "lead_id"),
    ("interview_events", "lead_id"),
    ("email_threads", "lead_id"),
    ("email_messages", "lead_id"),
    ("user_action_audits", "lead_id"),
)


def upgrade() -> None:
    _collapse_duplicate_source_identity_leads()
    with op.batch_alter_table("application_leads") as batch_op:
        batch_op.drop_index("ix_application_leads_source_identity")
        batch_op.create_unique_constraint(
            "uq_application_leads_user_source_identity",
            ["user_profile_id", "source_id", "source_external_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("application_leads") as batch_op:
        batch_op.drop_constraint("uq_application_leads_user_source_identity", type_="unique")
        batch_op.create_index(
            "ix_application_leads_source_identity",
            ["user_profile_id", "source_id", "source_external_id"],
            unique=False,
        )


def _collapse_duplicate_source_identity_leads() -> None:
    bind = op.get_bind()
    duplicate_groups = list(
        bind.execute(
            sa.text(
                """
                SELECT user_profile_id, source_id, source_external_id
                FROM application_leads
                WHERE source_id IS NOT NULL
                  AND source_external_id IS NOT NULL
                GROUP BY user_profile_id, source_id, source_external_id
                HAVING COUNT(*) > 1
                """
            )
        ).mappings()
    )

    for duplicate_group in duplicate_groups:
        lead_ids = list(
            bind.execute(
                sa.text(
                    """
                    SELECT id
                    FROM application_leads
                    WHERE user_profile_id = :user_profile_id
                      AND source_id = :source_id
                      AND source_external_id = :source_external_id
                    ORDER BY id ASC
                    """
                ),
                duplicate_group,
            ).scalars()
        )
        if len(lead_ids) < 2:
            continue

        survivor_id = lead_ids[0]
        for duplicate_id in lead_ids[1:]:
            _reassign_lead_references(bind, duplicate_id=duplicate_id, survivor_id=survivor_id)
            bind.execute(
                sa.text("DELETE FROM application_leads WHERE id = :duplicate_id"),
                {"duplicate_id": duplicate_id},
            )


def _reassign_lead_references(bind: sa.Connection, *, duplicate_id: int, survivor_id: int) -> None:
    for table_name, column_name in _LEAD_REFERENCE_TABLES:
        bind.execute(
            sa.text(f"UPDATE {table_name} SET {column_name} = :survivor_id WHERE {column_name} = :duplicate_id"),
            {"survivor_id": survivor_id, "duplicate_id": duplicate_id},
        )
