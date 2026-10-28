"""merge usage counters and quota pools

Revision ID: 7451a87d47cb
Revises: a4d6e8f0b2c3, b7c8d9e0f1a2
Create Date: 2026-10-28 11:32:56.042804

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7451a87d47cb'
down_revision: Union[str, Sequence[str], None] = ('a4d6e8f0b2c3', 'b7c8d9e0f1a2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
