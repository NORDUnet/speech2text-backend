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

from auth.oidc import get_current_user
from db.announcement import announcement_get_active
from db.customer import customer_get_from_user_id
from db.user import (
    user_get_private_key,
    user_update,
)

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from utils.log import get_logger
from utils.settings import get_settings
from utils.crypto import validate_private_key_password
from utils.validators import UserUpdateRequest

log = get_logger()
router = APIRouter(tags=["user"])
settings = get_settings()

api_file_storage_dir = settings.API_FILE_STORAGE_DIR


# Best-effort guess of a sensible language from a user's home-organisation
# domain (the realm), used only when neither the user nor their customer has set
# a default. Keys are country-code TLDs; values must be in
# SUPPORTED_TRANSCRIPTION_LANGUAGES. Unknown / generic TLDs (.net, .org, .com,
# .edu, .eu, ...) intentionally have no entry and fall through to the global
# default.
REALM_TLD_LANGUAGE: dict[str, str] = {
    "dk": "Danish",
    "fi": "Finnish",
    "se": "Swedish",
    "no": "Norwegian",
    "is": "Icelandic",
    "nl": "Dutch",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ua": "Ukrainian",
    "uk": "English",
    "ie": "English",
}


def _language_from_realm(realm: str | None) -> str | None:
    """
    Best-effort transcription language guessed from a realm's country-code TLD.

    Parameters:
        realm (str | None): The user's realm (home-organisation domain).

    Returns:
        str | None: A supported language, or None if the TLD is unknown/generic.
    """

    if not realm:
        return None

    tld = realm.strip().rstrip(".").rsplit(".", 1)[-1].lower()

    return REALM_TLD_LANGUAGE.get(tld)


async def _resolve_default_transcription_language(user: dict) -> str:
    """
    Resolve the effective default transcription language for a user.

    Cascade: user override -> customer/admin default -> realm-TLD guess
    -> system default.

    Parameters:
        user (dict): The current user.

    Returns:
        str: The effective default transcription language.
    """

    if user_default := user.get("default_transcription_language"):
        return user_default

    customer = await customer_get_from_user_id(user["user_id"])
    if customer and (customer_default := customer.get("default_transcription_language")):
        return customer_default

    if realm_default := _language_from_realm(user.get("realm")):
        return realm_default

    return settings.DEFAULT_TRANSCRIPTION_LANGUAGE


@router.get("/me")
async def get_user_info(
    request: Request,
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    """
    Get user information.
    Used by the frontend to get user information.

    Parameters:
        request (Request): The incoming HTTP request.
        user (dict): The current user.

    Returns:
        JSONResponse: The user information.
    """

    result = dict(user)
    # Expose the raw user-level override (null when inheriting) so the settings
    # page can distinguish "use organisation default" from an explicit choice,
    # and the effective value the transcribe dialogs should preselect.
    result["user_default_transcription_language"] = result.get(
        "default_transcription_language"
    )
    result["default_transcription_language"] = (
        await _resolve_default_transcription_language(user)
    )
    result["announcements"] = await announcement_get_active()
    return JSONResponse(content={"result": result})


@router.put("/me")
async def set_user_info(
    item: UserUpdateRequest,
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    """
    Set user information.
    Used by the frontend to set user information.

    Parameters:
        item (UserUpdateRequest): The user update data.
        user (dict): The current user.

    Returns:
        JSONResponse:  The result of the operation.
    """

    if item.encryption and item.encryption_password:
        await user_update(
            user["user_id"],
            encryption_settings=item.encryption,
            encryption_password=item.encryption_password,
        )
    elif item.reset_password:
        await user_update(user["user_id"], reset_encryption=True)
    elif item.verify_password:
        private_key = await user_get_private_key(user["user_id"])

        try:
            validate_private_key_password(private_key, item.encryption_password)
        except ValueError:
            log.info(
                f"Invalid private key password for user {user["user_id"]}"
            )
            return JSONResponse(
                content={"error": "Invalid private key or password"},
                status_code=403,
            )
    elif item.email is not None:
        await user_update(user["user_id"], email=item.email)
    elif item.notifications:
        notifications_str = ""

        if (
            item.notifications.notify_on_job is not None
            and item.notifications.notify_on_job
        ):
            notifications_str += "job,"
        if (
            item.notifications.notify_on_deletion is not None
            and item.notifications.notify_on_deletion
        ):
            notifications_str += "deletion,"
        if (
            item.notifications.notify_on_user is not None
            and item.notifications.notify_on_user
        ):
            notifications_str += "user,"
        if (
            item.notifications.notify_on_quota is not None
            and item.notifications.notify_on_quota
        ):
            notifications_str += "quota,"
        if (
            item.notifications.notify_on_weekly_report is not None
            and item.notifications.notify_on_weekly_report
        ):
            notifications_str += "weekly_report,"

        await user_update(user["user_id"], notifications_str=notifications_str)

    elif item.default_transcription_language is not None:
        language = item.default_transcription_language.strip()

        # An empty value clears the override; any non-empty value must be supported.
        if language and language not in settings.SUPPORTED_TRANSCRIPTION_LANGUAGES:
            return JSONResponse(
                content={"error": "Unsupported transcription language"},
                status_code=400,
            )

        await user_update(
            user["user_id"],
            default_transcription_language=language,
        )

    return JSONResponse(content={"result": {"status": "OK"}})


