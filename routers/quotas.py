"""BOFH-managed shared pools; realm admins have read-only aggregate access."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update

from auth.oidc import get_current_admin_user
from db.models import QuotaPool, QuotaRealm, QuotaUsage, QuotaCharge, QuotaConfigurationLock
from db.quota import insert_if_missing, lock_pool, lock_realm, month_start
from db.session import get_async_session

router = APIRouter(tags=["admin"])


class QuotaRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quota_seconds: int | None = Field(default=None, ge=0, le=9223372036854775807, strict=True)
    realms: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Name must not be empty")
        return value.strip()

    @field_validator("realms")
    @classmethod
    def clean_realms(cls, values):
        realms = sorted(set(v.strip() for v in values))
        if any(not v or v == "*" or "," in v for v in realms):
            raise ValueError("Specify individual, non-empty realm names")
        return realms


def admin_realms(user):
    # Match exact realm names, never substrings or implicit wildcards.
    return {s.strip() for s in (user.get("admin_domains") or "").split(",") if s.strip()}


@router.get("/admin/quotas")
async def get_quotas(admin_user: dict = Depends(get_current_admin_user)):
    period = month_start(datetime.now(UTC).replace(tzinfo=None))
    async with get_async_session() as session:
        query = select(QuotaPool)
        if not admin_user.get("bofh"):
            if not admin_user.get("admin"):
                raise HTTPException(403, "Administrator access required")
            realms = admin_realms(admin_user)
            current = select(QuotaRealm.quota_id).where(QuotaRealm.realm.in_(realms))
            historical = select(QuotaCharge.quota_id).where(
                QuotaCharge.realm.in_(realms), QuotaCharge.period_start == period,
            )
            query = query.where(QuotaPool.id.in_(current.union(historical)))
        pools = (await session.execute(query.order_by(QuotaPool.name))).scalars().all()
        ids = [p.id for p in pools]
        memberships = (await session.execute(select(QuotaRealm).where(QuotaRealm.quota_id.in_(ids)))).scalars().all()
        usage = {u.quota_id: u for u in (await session.execute(select(QuotaUsage).where(
            QuotaUsage.quota_id.in_(ids), QuotaUsage.period_start == period,
        ))).scalars()}
        result = []
        reset = datetime(period.year + (period.month == 12), period.month % 12 + 1, 1)
        for pool in pools:
            used = usage[pool.id].used_seconds if pool.id in usage else 0
            reserved = usage[pool.id].reserved_seconds if pool.id in usage else 0
            result.append({
                "id": pool.id, "name": pool.name, "quota_seconds": pool.quota_seconds,
                "realms": sorted(m.realm for m in memberships if m.quota_id == pool.id),
                "used_seconds": used, "reserved_seconds": reserved,
                "remaining_seconds": None if pool.quota_seconds is None else max(0, pool.quota_seconds - used - reserved),
                "period_start": period.isoformat() + "Z", "resets_at": reset.isoformat() + "Z",
            })
        return {"result": result}


async def save_quota(item, quota_id, admin_user):
    if not admin_user.get("bofh"):
        raise HTTPException(403, "Only BOFH can configure quotas")
    async with get_async_session() as session:
        # Serialize infrequent configuration changes, without serializing submissions
        # from different pools. Lock realm memberships before locking the pool.
        await insert_if_missing(session, QuotaConfigurationLock, {"id": 1}, ["id"])
        await session.execute(select(QuotaConfigurationLock).where(
            QuotaConfigurationLock.id == 1,
        ).with_for_update())
        current = (await session.execute(select(QuotaRealm.realm).where(
            QuotaRealm.quota_id == quota_id,
        ))).scalars().all() if quota_id is not None else []
        memberships = {}
        for realm in sorted(set(current) | set(item.realms)):
            memberships[realm] = await lock_realm(session, realm)
        for realm in item.realms:
            if memberships[realm].quota_id not in (None, quota_id):
                raise HTTPException(409, f"Realm {realm} already belongs to another quota. Remove it there first.")
        if quota_id is None:
            pool = QuotaPool(name=item.name, quota_seconds=item.quota_seconds)
            session.add(pool)
            await session.flush()
        else:
            pool = await lock_pool(session, quota_id)
            if pool is None:
                raise HTTPException(404, "Quota not found")
            pool.name = item.name
            pool.quota_seconds = item.quota_seconds
            await session.execute(update(QuotaUsage).where(
                QuotaUsage.quota_id == pool.id,
                QuotaUsage.period_start == month_start(datetime.now(UTC).replace(tzinfo=None)),
            ).values(quota_seconds=item.quota_seconds))
        for realm, membership in memberships.items():
            membership.quota_id = pool.id if realm in item.realms else None
        return {"result": {"id": pool.id}}


@router.post("/admin/quotas", status_code=201)
async def create_quota(item: QuotaRequest, admin_user: dict = Depends(get_current_admin_user)):
    return await save_quota(item, None, admin_user)


@router.put("/admin/quotas/{quota_id}")
async def update_quota(quota_id: int, item: QuotaRequest, admin_user: dict = Depends(get_current_admin_user)):
    return await save_quota(item, quota_id, admin_user)
