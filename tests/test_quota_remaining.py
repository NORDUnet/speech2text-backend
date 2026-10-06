"""Available shared quota is scoped to the current user and UTC month."""
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db import quota
from db.models import User, QuotaPool, QuotaRealm, QuotaUsage


class RemainingQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_remaining_quota_includes_reservations_and_current_membership(self):
        engine = create_engine('sqlite://')
        models = (User, QuotaPool, QuotaRealm, QuotaUsage)
        for model in models:
            model.__table__.create(engine)
        period = quota.month_start(datetime.now(UTC).replace(tzinfo=None))
        previous = quota.month_start(period - timedelta(days=1))
        try:
            with Session(engine) as session:
                for user_id in ('limited', 'new-month', 'unlimited', 'unassigned', 'blocked', 'overdrawn'):
                    session.execute(User.__table__.insert().values(
                        user_id=user_id, username=user_id, realm=user_id, transcribed_seconds=0))
                for pool_id, realm, limit in ((1, 'limited', 36000), (2, 'new-month', 72000),
                                              (3, 'unlimited', None), (4, 'blocked', 0),
                                              (5, 'overdrawn', 3600)):
                    session.add(QuotaPool(id=pool_id, name=realm, quota_seconds=limit))
                    session.add(QuotaRealm(realm=realm, quota_id=pool_id))
                session.add_all([
                    QuotaUsage(quota_id=1, period_start=period, used_seconds=14400, reserved_seconds=3600),
                    QuotaUsage(quota_id=1, period_start=previous, used_seconds=35000),
                    QuotaUsage(quota_id=2, period_start=previous, used_seconds=72000),
                    QuotaUsage(quota_id=5, period_start=period, used_seconds=3600, reserved_seconds=60),
                ])
                session.commit()

                @asynccontextmanager
                async def isolated_session():
                    yield SimpleNamespace(execute=AsyncMock(side_effect=session.execute))

                with patch.object(quota, 'get_async_session', isolated_session):
                    expected = {'limited': 18000, 'new-month': 72000, 'unlimited': None,
                                'unassigned': None, 'blocked': 0, 'overdrawn': 0}
                    for user_id, seconds in expected.items():
                        with self.subTest(user_id=user_id):
                            self.assertEqual(await quota.remaining_seconds_for_user(user_id), seconds)
        finally:
            engine.dispose()
