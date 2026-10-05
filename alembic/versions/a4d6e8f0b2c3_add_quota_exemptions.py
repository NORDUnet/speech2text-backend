"""Remember submissions made without an assigned quota pool.

Revision ID: a4d6e8f0b2c3
Revises: f2a4c6e8b0d1
"""
from alembic import op
import sqlalchemy as sa

revision = "a4d6e8f0b2c3"
down_revision = "f2a4c6e8b0d1"
branch_labels = None
depends_on = None


def upgrade():
    metadata = sa.MetaData()
    sa.Table("quota_exemptions", metadata, sa.Column("job_id", sa.String, primary_key=True))
    metadata.create_all(op.get_bind())


def downgrade():
    op.drop_table("quota_exemptions")
