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

from typing import Optional

from sqlalchemy import select

from db.models import Feedback
from db.session import get_async_session
from utils.log import get_logger

log = get_logger()


def _allowed_realms(admin_user: dict) -> Optional[list[str]]:
    """
    Return the realms an admin may see feedback for, or None for BOFH (all).
    Mirrors the realm scoping used by the other admin endpoints.
    """

    if admin_user.get("bofh"):
        return None

    admin_domains = admin_user.get("admin_domains", "") or ""
    allowed = [d.strip() for d in admin_domains.split(",") if d.strip()]
    if not allowed:
        allowed = [admin_user.get("realm", "")]

    return allowed


async def feedback_create(
    user_id: str,
    username: str,
    realm: str,
    category: str,
    message: str,
    page: Optional[str] = None,
) -> dict:
    """Store a new feedback entry."""

    async with get_async_session() as session:
        feedback = Feedback(
            user_id=user_id,
            username=username,
            realm=realm,
            category=category,
            message=message,
            page=page,
        )
        session.add(feedback)
        await session.flush()

        log.info(f"Feedback {feedback.id} ({category}) submitted by user {user_id}")
        return feedback.as_dict()


async def feedback_get(feedback_id: int) -> Optional[dict]:
    """Get a single feedback entry by ID."""

    async with get_async_session() as session:
        feedback = await session.get(Feedback, feedback_id)
        if not feedback:
            return None
        return feedback.as_dict()


async def feedback_get_all(admin_user: dict) -> list[dict]:
    """
    Get feedback entries visible to the given admin, newest first.
    BOFH sees all realms; realm admins see only their own realms.
    """

    async with get_async_session() as session:
        query = select(Feedback).order_by(Feedback.created_at.desc())

        allowed = _allowed_realms(admin_user)
        if allowed is not None:
            query = query.where(Feedback.realm.in_(allowed))

        result = await session.execute(query)
        return [f.as_dict() for f in result.scalars().all()]


async def feedback_update(
    feedback_id: int,
    admin_user: dict,
    status: Optional[str] = None,
    admin_note: Optional[str] = None,
) -> Optional[dict]:
    """
    Update the triage status and/or admin note of a feedback entry.
    Returns the updated entry, or None if not found or not visible to the admin.
    """

    async with get_async_session() as session:
        feedback = await session.get(Feedback, feedback_id)
        if not feedback:
            return None

        allowed = _allowed_realms(admin_user)
        if allowed is not None and feedback.realm not in allowed:
            log.warning(
                f"Admin {admin_user.get('user_id')} denied update of feedback "
                f"{feedback_id} (realm mismatch)"
            )
            return None

        if status is not None:
            feedback.status = status
        if admin_note is not None:
            feedback.admin_note = admin_note

        await session.flush()
        return feedback.as_dict()


async def feedback_delete(feedback_id: int, admin_user: dict) -> bool:
    """
    Delete a feedback entry. Returns False if not found or not visible
    to the admin.
    """

    async with get_async_session() as session:
        feedback = await session.get(Feedback, feedback_id)
        if not feedback:
            return False

        allowed = _allowed_realms(admin_user)
        if allowed is not None and feedback.realm not in allowed:
            log.warning(
                f"Admin {admin_user.get('user_id')} denied delete of feedback "
                f"{feedback_id} (realm mismatch)"
            )
            return False

        await session.delete(feedback)
        log.info(f"Feedback {feedback_id} deleted by admin {admin_user.get('user_id')}")
        return True
