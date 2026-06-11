"""Add default_transcription_language to users and customer tables.

Revision ID: e1f2a3b4c5d6
Revises: c4d5e6f7a8b9
Create Date: 2026-06-03 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    engine = op.get_bind()
    inspector = inspect(engine)

    user_columns = [x["name"] for x in inspector.get_columns("users")]
    if "default_transcription_language" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "default_transcription_language",
                sa.VARCHAR(),
                autoincrement=False,
                nullable=True,
            ),
        )

    customer_columns = [x["name"] for x in inspector.get_columns("customer")]
    if "default_transcription_language" not in customer_columns:
        op.add_column(
            "customer",
            sa.Column(
                "default_transcription_language",
                sa.VARCHAR(),
                autoincrement=False,
                nullable=True,
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    engine = op.get_bind()
    inspector = inspect(engine)

    user_columns = [x["name"] for x in inspector.get_columns("users")]
    if "default_transcription_language" in user_columns:
        op.drop_column("users", "default_transcription_language")

    customer_columns = [x["name"] for x in inspector.get_columns("customer")]
    if "default_transcription_language" in customer_columns:
        op.drop_column("customer", "default_transcription_language")
