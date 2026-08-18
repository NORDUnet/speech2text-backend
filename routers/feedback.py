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

from auth.oidc import get_current_admin_user, get_current_user
from db.feedback import (
    feedback_create,
    feedback_delete,
    feedback_get_all,
    feedback_update,
)
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from utils.log import get_logger
from utils.validators import CreateFeedbackRequest, UpdateFeedbackRequest

log = get_logger()
router = APIRouter(tags=["feedback"])

FEEDBACK_CATEGORIES = {"bug", "feature", "other"}
FEEDBACK_STATUSES = {"new", "reviewed", "planned", "done", "declined"}
FEEDBACK_MESSAGE_MAX_LENGTH = 5000


@router.post("/feedback")
async def submit_feedback(
    request: Request,
    item: CreateFeedbackRequest,
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    """
    Submit feedback (bug report, feature idea or other). Stored in the
    database for admin review; no notifications are sent.

    Parameters:
        request (Request): The incoming HTTP request.
        item (CreateFeedbackRequest): The feedback data.
        user (dict): The current user.

    Returns:
        JSONResponse: The stored feedback entry.
    """

    message = (item.message or "").strip()
    if not message:
        return JSONResponse(content={"error": "Message is required"}, status_code=400)

    if len(message) > FEEDBACK_MESSAGE_MAX_LENGTH:
        return JSONResponse(
            content={
                "error": f"Message must be at most {FEEDBACK_MESSAGE_MAX_LENGTH} characters"
            },
            status_code=400,
        )

    category = item.category if item.category in FEEDBACK_CATEGORIES else "other"

    # Anonymous feedback: the submitter must still be authenticated, but no
    # user identity is stored — only the realm, so admin scoping keeps working.
    created = await feedback_create(
        user_id="" if item.anonymous else user["user_id"],
        username="" if item.anonymous else user.get("username", ""),
        realm=user.get("realm", ""),
        category=category,
        message=message,
        page=item.page,
    )

    return JSONResponse(content={"result": created})


@router.get("/admin/feedback", include_in_schema=False)
async def list_feedback(
    request: Request,
    admin_user: dict = Depends(get_current_admin_user),
) -> JSONResponse:
    """
    List feedback entries. BOFH sees all realms; realm admins see only
    feedback from their own realms.

    Parameters:
        request (Request): The incoming HTTP request.
        admin_user (dict): The current admin user.

    Returns:
        JSONResponse: The list of feedback entries.
    """

    return JSONResponse(content={"result": await feedback_get_all(admin_user)})


@router.put("/admin/feedback/{feedback_id}", include_in_schema=False)
async def update_feedback(
    request: Request,
    feedback_id: int,
    item: UpdateFeedbackRequest,
    admin_user: dict = Depends(get_current_admin_user),
) -> JSONResponse:
    """
    Update the triage status and/or admin note of a feedback entry. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        feedback_id (int): The ID of the feedback entry.
        item (UpdateFeedbackRequest): The fields to update.
        admin_user (dict): The current admin user.

    Returns:
        JSONResponse: The updated feedback entry.
    """

    if not admin_user.get("bofh"):
        log.warning(
            f"Non-BOFH admin {admin_user['user_id']} denied feedback update"
        )
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    if item.status is not None and item.status not in FEEDBACK_STATUSES:
        return JSONResponse(
            content={"error": f"Status must be one of: {', '.join(sorted(FEEDBACK_STATUSES))}"},
            status_code=400,
        )

    updated = await feedback_update(
        feedback_id,
        admin_user,
        status=item.status,
        admin_note=item.admin_note,
    )

    if not updated:
        return JSONResponse(content={"error": "Feedback not found"}, status_code=404)

    return JSONResponse(content={"result": updated})


@router.delete("/admin/feedback/{feedback_id}", include_in_schema=False)
async def delete_feedback(
    request: Request,
    feedback_id: int,
    admin_user: dict = Depends(get_current_admin_user),
) -> JSONResponse:
    """
    Delete a feedback entry. BOFH only.

    Parameters:
        request (Request): The incoming HTTP request.
        feedback_id (int): The ID of the feedback entry.
        admin_user (dict): The current admin user.

    Returns:
        JSONResponse: The result of the operation.
    """

    if not admin_user.get("bofh"):
        log.warning(
            f"Non-BOFH admin {admin_user['user_id']} denied feedback delete"
        )
        return JSONResponse(content={"error": "User not authorized"}, status_code=403)

    if not await feedback_delete(feedback_id, admin_user):
        return JSONResponse(content={"error": "Feedback not found"}, status_code=404)

    return JSONResponse(content={"result": {"status": "OK"}})
