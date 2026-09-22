# Copyright (c) 2025-2026 Sunet.
# Contributor: Kristofer Hallin
#
# This file is part of Sunet Scribe.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from db.analytics import (
    get_page_views,
    get_page_views_summary,
    get_views_per_day,
    get_recent_views,
    get_hourly_heatmap,
    get_hourly_distribution,
    get_week_over_week,
    get_total_stats,
)

from utils.log import get_logger

from auth.oidc import get_current_admin_user

log = get_logger()
router = APIRouter(tags=["admin"])


@router.get("/admin/analytics/views")
async def analytics_views(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
    days: int = 30,
) -> JSONResponse:
    """
    Get page views grouped by path and day. BOFH only.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_page_views(days=days)})


@router.get("/admin/analytics/summary", include_in_schema=False)
async def analytics_summary(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
) -> JSONResponse:
    """
    Get total views per page (all time and last 30 days). BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.

    Returns:
        JSONResponse: The summary of page views.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_page_views_summary()})


@router.get("/admin/analytics/daily", include_in_schema=False)
async def analytics_daily(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
    days: int = 30,
) -> JSONResponse:
    """
    Get total views per day. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.
        days (int): Number of days to include in the aggregation.

    Returns:
        JSONResponse: The daily views data.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_views_per_day(days=days)})


@router.get("/admin/analytics/recent", include_in_schema=False)
async def analytics_recent(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
    limit: int = 50,
) -> JSONResponse:
    """
    Get most recent page views. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.
        limit (int): Maximum number of recent views to return.

    Returns:
        JSONResponse: The list of recent page views.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_recent_views(limit=limit)})


@router.get("/admin/analytics/heatmap", include_in_schema=False)
async def analytics_heatmap(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
    days: int = 30,
) -> JSONResponse:
    """
    Get views grouped by day-of-week and hour-of-day for heatmap. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.
        days (int): Number of days to include in the aggregation.

    Returns:
        JSONResponse: The heatmap data.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_hourly_heatmap(days=days)})


@router.get("/admin/analytics/hourly", include_in_schema=False)
async def analytics_hourly(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
    days: int = 30,
) -> JSONResponse:
    """
    Get views per hour-of-day. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.
        days (int): Number of days to include in the aggregation.

    Returns:
        JSONResponse: The hourly distribution of views.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_hourly_distribution(days=days)})


@router.get("/admin/analytics/wow", include_in_schema=False)
async def analytics_week_over_week(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
) -> JSONResponse:
    """
    Get week-over-week comparison. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.

    Returns:
        JSONResponse: The week-over-week comparison data.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_week_over_week()})


@router.get("/admin/analytics/stats", include_in_schema=False)
async def analytics_stats(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
) -> JSONResponse:
    """
    Get aggregate analytics stats for summary cards. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current user.

    Returns:
        JSONResponse: The aggregate analytics stats.
    """

    if not admin_user.get("bofh"):
        log.warning(f"Non-BOFH user {admin_user['user_id']} denied access to analytics")
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    return JSONResponse(content={"result": await get_total_stats()})


# Strict, bounded payload: arbitrary properties/identifiers cannot enter storage.
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from fastapi import HTTPException, Query
from auth.oidc import get_current_user
from utils.usage import UI_METRICS, record
from db.usage import summary as usage_summary


class UsageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counters: dict[str, StrictInt] = Field(max_length=32)

    @model_validator(mode="after")
    def validate_counters(self):
        if any(key not in UI_METRICS or not 1 <= value <= 10000
               for key, value in self.counters.items()):
            raise ValueError("Invalid usage counters")
        return self


@router.post("/analytics/usage", status_code=204, include_in_schema=False)
async def collect_usage(batch: UsageBatch, user: dict = Depends(get_current_user)):
    for metric, count in batch.counters.items():
        record(metric, count)


@router.get("/admin/analytics/usage", include_in_schema=False)
async def usage_statistics(weeks: int = Query(default=4, ge=1, le=52),
                           admin_user: dict = Depends(get_current_admin_user)):
    if not admin_user.get("bofh"):
        raise HTTPException(status_code=403, detail="BOFH access required")
    return {"result": await usage_summary(weeks)}
