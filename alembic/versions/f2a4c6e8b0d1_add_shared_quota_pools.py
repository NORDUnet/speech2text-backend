"""Shared realm quota pools and durable monthly accounting.

Revision ID: f2a4c6e8b0d1
Revises: e1f2a3b4c5d6
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a4c6e8b0d1"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    # checkfirst accommodates the application's metadata.create_all startup.
    metadata = sa.MetaData()
    sa.Table("quota_configuration_lock", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("quota_pools", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("quota_seconds", sa.BigInteger),
        sa.CheckConstraint("quota_seconds IS NULL OR quota_seconds >= 0"))
    sa.Table("quota_realms", metadata,
        sa.Column("realm", sa.String, primary_key=True),
        sa.Column("quota_id", sa.Integer, sa.ForeignKey("quota_pools.id"), index=True))
    sa.Table("quota_usage", metadata,
        sa.Column("quota_id", sa.Integer, sa.ForeignKey("quota_pools.id"), primary_key=True),
        sa.Column("period_start", sa.DateTime, primary_key=True),
        sa.Column("quota_seconds", sa.BigInteger),
        sa.Column("used_seconds", sa.BigInteger, nullable=False),
        sa.Column("reserved_seconds", sa.BigInteger, nullable=False),
        sa.CheckConstraint("used_seconds >= 0"), sa.CheckConstraint("reserved_seconds >= 0"))
    sa.Table("quota_charges", metadata,
        sa.Column("job_id", sa.String, primary_key=True),
        sa.Column("realm", sa.String, nullable=False, index=True),
        sa.Column("quota_id", sa.Integer, sa.ForeignKey("quota_pools.id")),
        sa.Column("submitted_at", sa.DateTime, nullable=False),
        sa.Column("period_start", sa.DateTime, nullable=False),
        sa.Column("duration_seconds", sa.Integer, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Index("ix_quota_charges_pool_period", "quota_id", "period_start"),
        sa.Index("ix_quota_charges_realm_period", "realm", "period_start", "quota_id"),
        sa.CheckConstraint("duration_seconds > 0"),
        sa.CheckConstraint("state IN ('reserved', 'completed', 'released')"))
    metadata.create_all(op.get_bind())


def downgrade():
    for table in ("quota_charges", "quota_usage", "quota_realms", "quota_pools", "quota_configuration_lock"):
        op.drop_table(table)
