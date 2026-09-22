"""Anonymous weekly usage counters."""
from alembic import op
import sqlalchemy as sa
revision = "b7c8d9e0f1a2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

def upgrade():
    if not sa.inspect(op.get_bind()).has_table("usage_counters"):
        op.create_table("usage_counters",
            sa.Column("week", sa.Date(), primary_key=True),
            sa.Column("metric", sa.String(64), primary_key=True),
            sa.Column("count", sa.Integer(), nullable=False, server_default="0"))

def downgrade():
    op.drop_table("usage_counters")
