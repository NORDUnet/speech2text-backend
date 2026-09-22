"""Persist batches, never individual actions. Statistics are best-effort."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from db.models import UsageCounter, Group, AttributeRule
from db.session import get_async_session
from utils.usage import drain

_flush_lock = asyncio.Lock()


async def flush():
    async with _flush_lock:
        batch = drain()
        if not batch:
            return
        try:
            async with asyncio.timeout(5):
                async with get_async_session() as session:
                    insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
                    statement = insert(UsageCounter).values([
                        dict(week=week, metric=metric, count=count)
                        for (week, metric), count in sorted(batch.items())
                    ])
                    await session.execute(statement.on_conflict_do_update(
                        index_elements=["week", "metric"],
                        set_={"count": UsageCounter.count + statement.excluded.count},
                    ))
        except Exception:
            # Do not retry an ambiguous commit or log payloads/authentication.
            logging.getLogger(__name__).warning("Usage statistics batch unavailable; counters may be incomplete")


def public_count(value):
    value = int(value)
    return "<5" if 0 < value < 5 else value


async def summary(weeks=4):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=today.weekday(), weeks=weeks-1)
    # Exclude the active week: avoids exposing a live activity feed.
    end = today - timedelta(days=today.weekday())
    start -= timedelta(weeks=1)
    async with get_async_session() as session:
        rows = (await session.execute(select(UsageCounter.metric, func.sum(UsageCounter.count))
            .where(UsageCounter.week >= start, UsageCounter.week < end)
            .group_by(UsageCounter.metric))).all()
        gauges = {}
        for name, model, condition in (
            ("Groups currently created", Group, None),
            ("Groups with transcription limits", Group, Group.quota_seconds > 0),
            ("Enabled provisioning rules", AttributeRule, AttributeRule.enabled == True),
        ):
            stmt = select(func.count()).select_from(model)
            if condition is not None:
                stmt = stmt.where(condition)
            gauges[name] = public_count((await session.execute(stmt)).scalar_one())
    return {"start": str(start), "end": str(end),
            "counters": {metric: public_count(count) for metric, count in rows},
            "current": gauges}
