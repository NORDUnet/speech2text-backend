"""Anonymous statistics: isolated DB, access controls and queue commit semantics."""
import unittest
from contextlib import asynccontextmanager
from datetime import date, timedelta, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from db.models import UsageCounter, Group, AttributeRule
from db import usage, job
from utils.usage import record, drain, size_metric
from routers import analytics


class CounterTests(unittest.TestCase):
    def setUp(self):
        drain()

    def test_bounded_categories_and_sizes(self):
        record("filename-secret", 1)
        record("queued.transcript", True)
        record("queued.transcript", -1)
        self.assertEqual(drain(), {})
        for size, index in [(0,0), (100*1024**2-1,0), (100*1024**2,1),
                            (500*1024**2,2), (1024**3,3), (2*1024**3,4)]:
            self.assertEqual(size_metric(size), f"upload.size.{index}")
        record("queued.transcript", 2)
        batch = drain()
        self.assertEqual(len(batch), 1)
        (week, metric), count = next(iter(batch.items()))
        self.assertEqual(week.weekday(), 0)
        self.assertEqual((metric,count), ("queued.transcript",2))
        self.assertEqual(drain(), {})

    def test_routes_authorization_and_payload_validation(self):
        app = FastAPI()
        app.include_router(analytics.router)
        app.dependency_overrides[analytics.get_current_user] = lambda: {"user_id":"not-stored"}
        app.dependency_overrides[analytics.get_current_admin_user] = lambda: {"bofh":False}
        with TestClient(app) as client:
            self.assertEqual(client.get("/admin/analytics/usage").status_code,403)
            for payload in [
                {"counters":{"preview.enabled":1},"user_id":"secret"},
                {"counters":{"queued.transcript":1}},
                {"counters":{"preview.enabled":True}},
                {"counters":{"preview.enabled":10001}},
                {"counters":{"preview.enabled":0}},
                {"counters":{"filename":1}},
            ]:
                self.assertEqual(client.post("/analytics/usage",json=payload).status_code,422)
            self.assertEqual(client.post("/analytics/usage",json={"counters":{"preview.enabled":2}}).status_code,204)
            app.dependency_overrides[analytics.get_current_admin_user] = lambda: {"bofh":True}
            with patch.object(analytics,'usage_summary',AsyncMock(return_value={"counters":{}})):
                self.assertEqual(client.get("/admin/analytics/usage").status_code,200)
                self.assertEqual(client.get("/admin/analytics/usage?weeks=10000").status_code,422)
        self.assertEqual(sum(drain().values()),2)


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        drain()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda c: UsageCounter.__table__.create(c))
            await conn.run_sync(lambda c: Group.__table__.create(c))
            await conn.run_sync(lambda c: AttributeRule.__table__.create(c))
        factory=async_sessionmaker(self.engine,expire_on_commit=False)
        @asynccontextmanager
        async def session():
            async with factory() as s:
                yield s
                await s.commit()
        self.session=session
        self.patcher=patch.object(usage,'get_async_session',session)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        await self.engine.dispose()
        drain()

    async def test_upsert_and_private_summary(self):
        record("queued.transcript",2)
        await usage.flush()
        record("queued.transcript",3)
        await usage.flush()
        async with self.session() as s:
            rows=(await s.execute(select(UsageCounter))).scalars().all()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].count,5)
            week=rows[0].week
            s.add(UsageCounter(week=week-timedelta(weeks=1),metric="queued.subtitles",count=3))
            s.add(UsageCounter(week=week-timedelta(weeks=5),metric="queued.subtitles",count=99))
        result=await usage.summary(4)
        self.assertEqual(result['counters'],{'queued.subtitles':'<5'})
        self.assertEqual(set(UsageCounter.__table__.columns.keys()),{'week','metric','count'})

    async def test_failed_flush_does_not_raise_or_retry(self):
        record("queued.transcript")
        @asynccontextmanager
        async def broken():
            raise RuntimeError('unavailable')
            yield
        with patch.object(usage,'get_async_session',broken):
            await usage.flush()
        self.assertEqual(drain(),{})

    async def test_member_additions_and_provisioning_only_count_new_links(self):
        from db import group
        from unittest.mock import Mock
        def result(value):
            return SimpleNamespace(scalars=lambda:SimpleNamespace(first=lambda:value))
        for existing in (None, SimpleNamespace(group_id=1)):
            session=SimpleNamespace(execute=AsyncMock(side_effect=[result(SimpleNamespace(id=1)), result(existing)]),add=Mock())
            @asynccontextmanager
            async def isolated():
                yield session
                self.assertEqual(drain(),{})
            with patch.object(group,'get_async_session',isolated):
                await group.group_add_user(1,'not-stored',provisioning=True)
            counts={metric: count for (_,metric),count in drain().items()}
            self.assertEqual(counts, {} if existing else {"group.member_added":1,"provision.changed":1})

    async def test_queue_count_only_after_commit_and_not_repeated(self):
        row=SimpleNamespace(uuid='job',status='uploaded',output_format='srt')
        row.as_dict=lambda:dict(status=row.status,output_format=row.output_format)
        session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(first=lambda:row))))
        @asynccontextmanager
        async def success():
            yield session
            self.assertEqual(drain(), {})  # not before transaction exits
        with patch.object(job,'get_async_session',success):
            await job.job_update('job',status='pending')
        self.assertEqual(sum(drain().values()),1)
        with patch.object(job,'get_async_session',success):
            await job.job_update('job',status='pending')
        self.assertEqual(drain(),{})
        row.status='uploaded'
        @asynccontextmanager
        async def failure():
            yield session
            raise RuntimeError('commit failed')
        with patch.object(job,'get_async_session',failure):
            with self.assertRaises(RuntimeError):
                await job.job_update('job',status='pending')
        self.assertEqual(drain(),{})

if __name__ == '__main__':
    unittest.main()
