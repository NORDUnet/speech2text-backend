"""Browser upload identities must not inherit the REACH quota exemption."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from db import quota
from db.models import JobStatusEnum


@pytest.mark.parametrize("external_id, expected", [(None, True), ("ui-upload:abc", True), ("reach", False)])
def test_duration_preflight(external_id, expected):
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        first=lambda: (external_id, None, None, 1)
    )))

    @asynccontextmanager
    async def isolated_session():
        yield session

    with patch.object(quota, "get_async_session", isolated_session):
        assert asyncio.run(quota.needs_duration("job", "owner")) is expected


@pytest.mark.parametrize("duration, rejected", [(60, False), (101, True)])
def test_browser_upload_reserves_capacity(duration, rejected):
    job = SimpleNamespace(uuid="job", external_id="ui-upload:abc", status=JobStatusEnum.UPLOADED, user_id="owner")
    usage = SimpleNamespace(quota_seconds=100, used_seconds=0, reserved_seconds=0)
    session = SimpleNamespace(
        get=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: SimpleNamespace(realm="realm"))),
        add=Mock(), flush=AsyncMock(),
    )
    with patch.object(quota, "lock_realm", AsyncMock(return_value=SimpleNamespace(quota_id=1))), \
         patch.object(quota, "lock_pool", AsyncMock(return_value=SimpleNamespace(id=1, quota_seconds=100))), \
         patch.object(quota, "usage_row", AsyncMock(return_value=usage)):
        if rejected:
            with pytest.raises(HTTPException) as error:
                asyncio.run(quota.reserve(session, job, duration))
            assert error.value.status_code == 403
            assert usage.reserved_seconds == 0
        else:
            asyncio.run(quota.reserve(session, job, duration))
            assert usage.reserved_seconds == duration
            charge = session.add.call_args.args[0]
            assert charge.state == "reserved"
            assert charge.duration_seconds == duration


def test_reach_submission_remains_exempt():
    session = SimpleNamespace(get=AsyncMock())
    job = SimpleNamespace(external_id="reach")
    asyncio.run(quota.reserve(session, job, None))
    session.get.assert_not_awaited()
