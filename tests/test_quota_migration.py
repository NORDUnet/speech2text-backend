import importlib.util
import os
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
import pytest
from sqlalchemy.exc import IntegrityError


def test_quota_migration_upgrade_repeat_and_downgrade():
    url = os.environ.get("QUOTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set QUOTA_TEST_DATABASE_URL to a disposable PostgreSQL database")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("QUOTA_TEST_DATABASE_URL must use postgresql+asyncpg://")
    path = Path(__file__).parents[1] / "alembic/versions/f2a4c6e8b0d1_add_shared_quota_pools.py"
    spec = importlib.util.spec_from_file_location("quota_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    exemption_path = path.with_name("a4d6e8f0b2c3_add_quota_exemptions.py")
    exemption_spec = importlib.util.spec_from_file_location("exemption_migration", exemption_path)
    exemption_migration = importlib.util.module_from_spec(exemption_spec)
    exemption_spec.loader.exec_module(exemption_migration)
    engine = create_engine(make_url(url).set(drivername="postgresql+psycopg2"))
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        exemption_migration.op = migration.op
        exemption_migration.upgrade()
        exemption_migration.upgrade()
        expected = {"quota_pools", "quota_realms", "quota_usage", "quota_charges", "quota_configuration_lock", "quota_exemptions"}
        assert set(inspect(connection).get_table_names()) == expected
        connection.execute(text("INSERT INTO quota_pools (id, name, quota_seconds) VALUES (1, 'shared', 3600)"))
        connection.execute(text("INSERT INTO quota_realms (realm, quota_id) VALUES ('example', 1)"))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("INSERT INTO quota_realms (realm, quota_id) VALUES ('example', 1)"))
        exemption_migration.downgrade()
        migration.downgrade()
        assert inspect(connection).get_table_names() == []
    engine.dispose()
