"""Shared monthly quotas. All mutations run in the caller's transaction.

Lock order: job, realm, pool. A pool row serializes its counters and limit edits.
No media I/O may occur while these locks are held.
"""
from datetime import UTC, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from db.session import get_async_session
from db.models import JobStatusEnum, QuotaCharge, QuotaPool, QuotaRealm, QuotaUsage, QuotaExemption, Job, User


class MediaDurationRequired(Exception):
    """Membership changed after the read-only duration preflight."""


def is_external_job(external_id: Optional[str]) -> bool:
    """Browser upload identities follow normal quota accounting."""
    return external_id is not None and not external_id.startswith("ui-upload:")


async def needs_duration(job_id, user_id):
    async with get_async_session() as session:
        # One indexed lookup/join query; admission rechecks membership under lock.
        row = (await session.execute(
            select(Job.external_id, QuotaExemption.job_id, QuotaCharge.job_id, QuotaRealm.quota_id)
            .join(User, User.user_id == Job.user_id)
            .outerjoin(QuotaExemption, QuotaExemption.job_id == Job.uuid)
            .outerjoin(QuotaCharge, QuotaCharge.job_id == Job.uuid)
            .outerjoin(QuotaRealm, QuotaRealm.realm == User.realm)
            .where(Job.uuid == job_id, Job.user_id == user_id)
        )).first()
        if row is None:
            raise HTTPException(404, "Job not found")
        external_id, exemption, existing_charge, quota_id = row
        return (
            not is_external_job(external_id)
            and exemption is None
            and existing_charge is None
            and quota_id is not None
        )


def month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def insert_if_missing(session, model, values, keys):
    await session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=keys))


async def lock_pool(session, quota_id):
    # FOR NO KEY UPDATE serializes accounting without conflicting with the
    # foreign-key key-share locks acquired when inserting charge records.
    return await session.scalar(
        select(QuotaPool).where(QuotaPool.id == quota_id)
        .with_for_update(key_share=True).execution_options(populate_existing=True)
    )


async def lock_realm(session, realm):
    await insert_if_missing(session, QuotaRealm, {"realm": realm}, ["realm"])
    return await session.scalar(
        select(QuotaRealm).where(QuotaRealm.realm == realm)
        .with_for_update().execution_options(populate_existing=True)
    )


async def usage_row(session, quota_id, period, limit=None):
    await insert_if_missing(session, QuotaUsage, {
        "quota_id": quota_id, "period_start": period,
        "used_seconds": 0, "reserved_seconds": 0, "quota_seconds": limit,
    }, ["quota_id", "period_start"])
    return await session.get(QuotaUsage, (quota_id, period), populate_existing=True)


async def reserve(session, job, duration_seconds):
    # REACH jobs are exempt; browser upload identities are charged normally.
    if is_external_job(job.external_id):
        return
    if job.status not in (JobStatusEnum.UPLOADED, JobStatusEnum.FAILED):
        raise HTTPException(409, "Only uploaded or failed jobs can be submitted.")
    if await session.get(QuotaExemption, job.uuid):
        return
    charge = await session.get(QuotaCharge, job.uuid)
    if charge and charge.state == "reserved":
        return  # Retried submission, not a second execution.
    if charge and charge.state == "completed":
        raise HTTPException(409, "Completed jobs cannot be submitted again; upload a new job.")
    if not charge:
        user = (await session.execute(select(User).where(User.user_id == job.user_id))).scalar_one()
        membership = await lock_realm(session, user.realm)
        if membership.quota_id is None:
            session.add(QuotaExemption(job_id=job.uuid))
            await session.flush()
            return
        if duration_seconds is None:
            raise MediaDurationRequired()
        if duration_seconds <= 0:
            raise HTTPException(422, "A verified media duration is required.")
        submitted_at = datetime.now(UTC).replace(tzinfo=None)
        charge = QuotaCharge(
            job_id=job.uuid, realm=user.realm, quota_id=membership.quota_id,
            submitted_at=submitted_at, period_start=month_start(submitted_at),
            duration_seconds=duration_seconds,
        )
        session.add(charge)
    if charge.quota_id is not None:
        pool = await lock_pool(session, charge.quota_id)
        usage = await usage_row(session, pool.id, charge.period_start, pool.quota_seconds)
        if usage.quota_seconds is not None and (
            usage.used_seconds + usage.reserved_seconds + charge.duration_seconds > usage.quota_seconds
        ):
            raise HTTPException(403, {
                "code": "quota_exceeded", "message": "Shared monthly transcription quota exceeded. Contact your administrator.",
                "quota_seconds": usage.quota_seconds,
                "used_seconds": usage.used_seconds, "reserved_seconds": usage.reserved_seconds,
            })
        usage.reserved_seconds += charge.duration_seconds
    charge.state = "reserved"
    await session.flush()


async def settle(session, job, status):
    """Settle exactly once, retaining the verified full media duration."""
    charge = await session.get(QuotaCharge, job.uuid)
    if not charge or charge.state != "reserved":
        return
    if status not in (JobStatusEnum.COMPLETED, JobStatusEnum.FAILED, JobStatusEnum.DELETED):
        return
    if charge.quota_id is not None:
        await lock_pool(session, charge.quota_id)
        usage = await usage_row(session, charge.quota_id, charge.period_start)
        usage.reserved_seconds -= charge.duration_seconds
        if status == JobStatusEnum.COMPLETED:
            usage.used_seconds += charge.duration_seconds
    charge.state = "completed" if status == JobStatusEnum.COMPLETED else "released"
