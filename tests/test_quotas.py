"""Quota integration tests run against an isolated PostgreSQL database.

QUOTA_TEST_DATABASE_URL must name a disposable async PostgreSQL database. Never use
an application database: the fixture creates and drops all model tables.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel

from db import job as jobs
from db import quota as accounting
from db.models import Job, JobStatusEnum, JobType, User, QuotaCharge, QuotaUsage, QuotaExemption
from routers import quotas

BOFH = {"bofh": True, "admin": True}
ADMIN = {"bofh": False, "admin": True, "admin_domains": "a.example"}


async def scenario(tmp_path, monkeypatch, run):
    url = os.environ.get("QUOTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set QUOTA_TEST_DATABASE_URL to a disposable PostgreSQL database")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("QUOTA_TEST_DATABASE_URL must use postgresql+asyncpg://")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_scope():
        async with factory.begin() as session:
            yield session

    monkeypatch.setattr(jobs, "get_async_session", session_scope)
    monkeypatch.setattr(quotas, "get_async_session", session_scope)
    monkeypatch.setattr(accounting, "get_async_session", session_scope)
    monkeypatch.setattr(jobs.settings, "API_FILE_STORAGE_DIR", str(tmp_path))
    try:
        async with factory.begin() as session:
            session.add_all([User(user_id=r, username=r, realm=r, transcribed_seconds=0) for r in ("a.example", "b.example", "c.example")])
        await run(factory)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
        await engine.dispose()


async def pool(limit=3600, realms=None, name="Shared"):
    result = await quotas.create_quota(quotas.QuotaRequest(
        name=name, quota_seconds=limit, realms=realms if realms is not None else ["a.example", "b.example"],
    ), BOFH)
    return result["result"]["id"]


async def job(factory, realm="a.example", external=False):
    async with factory.begin() as session:
        j = Job(user_id=realm, job_type=JobType.TRANSCRIPTION, status=JobStatusEnum.UPLOADED,
                external_id="reach" if external else None)
        session.add(j)
        await session.flush()
        return j.uuid


async def stats(user=BOFH):
    return (await quotas.get_quotas(user))["result"]


def test_shared_capacity_idempotency_and_reach_exclusion(tmp_path, monkeypatch):
    async def run(factory):
        await pool(100)
        a, b, reach = await job(factory), await job(factory, "b.example"), await job(factory, external=True)
        await jobs.job_update(a, status="pending", duration_seconds=60)
        await jobs.job_update(a, status="pending", duration_seconds=60)
        with pytest.raises(HTTPException) as error:
            await jobs.job_update(b, status="pending", duration_seconds=41)
        assert error.value.status_code == 403
        await jobs.job_update(b, status="pending", duration_seconds=40)
        await jobs.job_update(reach, status="pending")
        await jobs.job_update(reach, status="completed", transcribed_seconds=500)
        assert (await stats())[0]["reserved_seconds"] == 100
        await jobs.job_update(a, status="in_progress")
        await jobs.job_update(a, status="pending", duration_seconds=60)
        assert (await jobs.job_get(a, "a.example"))["status"] == "in_progress"
        await jobs.job_update(a, status="completed", transcribed_seconds=55)
        await jobs.job_update(a, status="completed", transcribed_seconds=55)
        row = (await stats())[0]
        assert (row["used_seconds"], row["reserved_seconds"], row["remaining_seconds"]) == (60, 40, 0)
        async with factory() as session:
            user = (await session.execute(select(User).where(User.user_id == "a.example"))).scalar_one()
            assert user.transcribed_seconds == 555  # existing user accounting also idempotent
            assert await session.get(QuotaCharge, reach) is None
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_failure_deletion_and_retry(tmp_path, monkeypatch):
    async def run(factory):
        await pool(100)
        a = await job(factory)
        await jobs.job_update(a, status="pending", duration_seconds=60)
        await jobs.job_update(a, status="failed")
        await jobs.job_update(a, status="failed")
        assert (await stats())[0]["reserved_seconds"] == 0
        await jobs.job_update(a, status="pending", duration_seconds=60)
        assert (await stats())[0]["reserved_seconds"] == 60
        await jobs.job_remove(a)
        assert (await stats())[0]["reserved_seconds"] == 0
        with pytest.raises(HTTPException):
            await jobs.job_update(a, status="completed", transcribed_seconds=60)
        b = await job(factory)
        await jobs.job_update(b, status="pending", duration_seconds=40)
        await jobs.job_update(b, status="in_progress")
        with pytest.raises(HTTPException):
            await jobs.job_remove(b)
        await jobs.job_update(b, status="completed", transcribed_seconds=40)
        await jobs.job_remove(b)
        assert (await stats())[0]["used_seconds"] == 40
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_permissions_unique_membership_and_immediate_limit_edits(tmp_path, monkeypatch):
    async def run(factory):
        pid = await pool(100)
        request = quotas.QuotaRequest(name="Edited", quota_seconds=30, realms=["a.example", "b.example"])
        with pytest.raises(HTTPException) as error:
            await quotas.update_quota(pid, request, ADMIN)
        assert error.value.status_code == 403
        with pytest.raises(HTTPException) as error:
            await pool(50, ["a.example"])
        assert error.value.status_code == 409
        assert len(await stats(ADMIN)) == 1
        assert await stats({**ADMIN, "admin_domains": "example"}) == []
        a = await job(factory)
        await jobs.job_update(a, status="pending", duration_seconds=60)
        await quotas.update_quota(pid, request, BOFH)
        assert (await stats())[0]["quota_seconds"] == 30
        assert (await stats())[0]["reserved_seconds"] == 60
        b = await job(factory)
        with pytest.raises(HTTPException):
            await jobs.job_update(b, status="pending", duration_seconds=1)
        await jobs.job_update(a, status="completed", transcribed_seconds=60)
        await quotas.update_quota(pid, request.model_copy(update={"quota_seconds": 100}), BOFH)
        await jobs.job_update(b, status="pending", duration_seconds=40)
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_realm_changes_preserve_original_assignment(tmp_path, monkeypatch):
    async def run(factory):
        old = await pool(100)
        a = await job(factory)
        await jobs.job_update(a, status="pending", duration_seconds=60)
        await quotas.update_quota(old, quotas.QuotaRequest(name="Old", quota_seconds=100, realms=["b.example"]), BOFH)
        new = await pool(200, ["a.example"], "New")
        b = await job(factory)
        await jobs.job_update(b, status="pending", duration_seconds=80)
        await jobs.job_update(a, status="completed", transcribed_seconds=60)
        rows = {r["id"]: r for r in await stats()}
        assert rows[old]["used_seconds"] == 60
        assert rows[new]["reserved_seconds"] == 80
        assert len(await stats(ADMIN)) == 2  # retained current-month charges remain visible
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_concurrent_reservations_never_overspend(tmp_path, monkeypatch):
    async def run(factory):
        await pool(100)
        ids = [await job(factory, "a.example" if i % 2 else "b.example") for i in range(8)]
        results = await asyncio.gather(*[
            jobs.job_update(j, status="pending", duration_seconds=30) for j in ids
        ], return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 3, results
        assert all(isinstance(r, dict) or isinstance(r, HTTPException) and r.status_code == 403 for r in results), results
        assert (await stats())[0]["reserved_seconds"] == 90
        admitted = [r["uuid"] for r in results if isinstance(r, dict)]
        await asyncio.gather(*[
            jobs.job_update(j, status="completed", transcribed_seconds=30) for j in admitted for _ in range(3)
        ])
        assert ((await stats())[0]["used_seconds"], (await stats())[0]["reserved_seconds"]) == (90, 0)
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_unlimited_zero_and_unassigned_realms(tmp_path, monkeypatch):
    async def run(factory):
        pid = await pool(0, ["a.example"])
        a = await job(factory)
        with pytest.raises(HTTPException):
            await jobs.job_update(a, status="pending", duration_seconds=1)
        await quotas.update_quota(pid, quotas.QuotaRequest(name="Unlimited", quota_seconds=None, realms=["a.example"]), BOFH)
        await jobs.job_update(a, status="pending", duration_seconds=100000)
        b = await job(factory, "c.example")
        await jobs.job_update(b, status="pending", duration_seconds=99)
        await jobs.job_update(b, status="completed", transcribed_seconds=99)
        assert (await stats())[0]["reserved_seconds"] == 100000
        assert (await stats())[0]["remaining_seconds"] is None
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_month_boundary_uses_submission_period(tmp_path, monkeypatch):
    async def run(factory):
        import db.quota as accounting
        class January(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2025, 1, 31, 23, 59, tzinfo=tz)
        monkeypatch.setattr(accounting, "datetime", January)
        pid = await pool(100)
        a = await job(factory)
        await jobs.job_update(a, status="pending", duration_seconds=60)
        monkeypatch.setattr(accounting, "datetime", datetime)
        # Current-month changes must not rewrite the previous month's allowance.
        await quotas.update_quota(pid, quotas.QuotaRequest(name="Shared", quota_seconds=10, realms=["a.example", "b.example"]), BOFH)
        await jobs.job_update(a, status="completed", transcribed_seconds=60)
        assert (await stats())[0]["used_seconds"] == 0
        async with factory() as session:
            usage = await session.get(QuotaUsage, (pid, datetime(2025, 1, 1)))
            assert (usage.used_seconds, usage.reserved_seconds, usage.quota_seconds) == (60, 0, 100)
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_result_upload_does_not_complete_before_final_worker_callback(tmp_path, monkeypatch):
    async def run(factory):
        from unittest.mock import AsyncMock
        from routers import job as worker
        from utils.validators import TranscriptionResultRequest
        await pool(100)
        job_id = await job(factory)
        await jobs.job_update(job_id, status="pending", duration_seconds=60)
        await jobs.job_update(job_id, status="in_progress")
        monkeypatch.setattr(worker, "user_get_public_key", AsyncMock(return_value="key"))
        monkeypatch.setattr(worker, "deserialize_public_key_from_pem", lambda key: key)
        monkeypatch.setattr(worker, "encrypt_string", lambda key, data: "encrypted")
        monkeypatch.setattr(worker, "job_result_save", AsyncMock())
        for format in ("srt", "json"):
            response = await worker.put_transcription_result(
                None, TranscriptionResultRequest(format=format, result="text"),
                "a.example", job_id, "worker",
            )
            assert response.status_code == 200
            assert (await jobs.job_get(job_id, "a.example"))["status"] == "in_progress"
            assert (await stats())[0]["reserved_seconds"] == 60
        await jobs.job_update(job_id, status="completed", transcribed_seconds=55)
        assert (await stats())[0]["used_seconds"] == 60
        async with factory() as session:
            user = (await session.execute(select(User).where(User.user_id == "a.example"))).scalar_one()
            assert user.transcribed_seconds == 55
    asyncio.run(scenario(tmp_path, monkeypatch, run))


async def submit_via_route(monkeypatch, job_id):
    from unittest.mock import AsyncMock
    from routers import transcriber
    from utils.validators import TranscriptionStatusPut
    monkeypatch.setattr(transcriber, "user_get_quota_left", AsyncMock(return_value=True))
    monkeypatch.setattr(transcriber, "user_get_private_key", AsyncMock(side_effect=ValueError))
    return await transcriber.update_transcription_status(
        None, TranscriptionStatusPut(), job_id, {"user_id": "c.example"},
    )


def test_unassigned_submission_skips_probing_and_remains_exempt(tmp_path, monkeypatch):
    async def run(factory):
        from unittest.mock import AsyncMock
        from routers import transcriber
        probe = AsyncMock(side_effect=AssertionError("Unassigned jobs must not probe media"))
        monkeypatch.setattr(transcriber, "probe_duration", probe)
        job_id = await job(factory, "c.example")
        assert (await submit_via_route(monkeypatch, job_id)).status_code == 200
        async with factory() as session:
            assert await session.get(QuotaExemption, job_id) is not None
            assert await session.get(QuotaCharge, job_id) is None
        await pool(0, ["c.example"])
        await jobs.job_update(job_id, status="failed")
        assert (await submit_via_route(monkeypatch, job_id)).status_code == 200
        await jobs.job_update(job_id, status="completed", transcribed_seconds=99)
        probe.assert_not_awaited()
        assert (await stats())[0]["used_seconds"] == 0
        assert (await stats())[0]["reserved_seconds"] == 0
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_pool_added_during_preflight_cannot_bypass_reservation(tmp_path, monkeypatch):
    async def run(factory):
        from unittest.mock import AsyncMock
        from routers import transcriber
        probe = AsyncMock(return_value=60)
        monkeypatch.setattr(transcriber, "probe_duration", probe)
        job_id = await job(factory, "c.example")
        async def preflight_then_assign(job_id, user_id):
            required = await accounting.needs_duration(job_id, user_id)
            assert required is False
            await pool(100, ["c.example"])
            return required
        monkeypatch.setattr(transcriber, "needs_duration", preflight_then_assign)
        assert (await submit_via_route(monkeypatch, job_id)).status_code == 200
        probe.assert_awaited_once()
        assert (await stats())[0]["reserved_seconds"] == 60
        async with factory() as session:
            assert await session.get(QuotaExemption, job_id) is None
    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_assigned_unlimited_pool_still_tracks_duration(tmp_path, monkeypatch):
    async def run(factory):
        from unittest.mock import AsyncMock
        from routers import transcriber
        await pool(None, ["c.example"])
        probe = AsyncMock(return_value=60)
        monkeypatch.setattr(transcriber, "probe_duration", probe)
        job_id = await job(factory, "c.example")
        assert (await submit_via_route(monkeypatch, job_id)).status_code == 200
        probe.assert_awaited_once()
        assert (await stats())[0]["reserved_seconds"] == 60
    asyncio.run(scenario(tmp_path, monkeypatch, run))
