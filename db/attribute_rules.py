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

from utils.usage import record
from utils.provisioning import evaluate_provisioning, claim_values as _get_claim_values, match_condition as _match_condition
from typing import Optional

from sqlalchemy import select

from db.group import group_add_user
from db.models import AttributeConditionEnum, AttributeRule, Group, GroupUserLink, User
from db.session import get_async_session
from utils.log import get_logger

log = get_logger()


async def rule_create(
    name: str,
    attribute_name: str,
    attribute_condition: str,
    attribute_value: str,
    realm: Optional[str] = None,
    activate: bool = False,
    admin: bool = False,
    deny: bool = False,
    assign_to_group: Optional[str] = None,
    owner_domains: Optional[str] = None,
    enabled: bool = True,
    user_id: Optional[str] = None,
) -> dict:
    """Create a new attribute rule."""

    async with get_async_session() as session:
        rule = AttributeRule(
            name=name,
            attribute_name=attribute_name,
            attribute_condition=AttributeConditionEnum(attribute_condition),
            attribute_value=attribute_value,
            realm=realm,
            activate=activate,
            admin=admin,
            deny=deny,
            assign_to_group=assign_to_group,
            owner_domains=owner_domains,
            enabled=enabled,
        )
        session.add(rule)
        await session.flush()
        log.info(f"Attribute rule {rule.id} created by user {user_id}.")
        return rule.as_dict()


async def rule_get(rule_id: int) -> Optional[dict]:
    """Get a single attribute rule by ID."""

    async with get_async_session() as session:
        result = await session.execute(
            select(AttributeRule).where(AttributeRule.id == rule_id)
        )
        rule = result.scalars().first()
        return rule.as_dict() if rule else None


async def rule_get_all(realm: Optional[str | list[str]] = None) -> list[dict]:
    """Get all attribute rules, optionally filtered by realm(s).

    For comma-separated realm values in rules, checks if any of the
    rule's realms overlap with the requested realm(s).
    """

    async with get_async_session() as session:
        result = await session.execute(
            select(AttributeRule).order_by(AttributeRule.id)
        )
        rules = result.scalars().all()
        all_rules = [r.as_dict() for r in rules]

    if not realm or realm == "*":
        return all_rules

    allowed = set(realm) if isinstance(realm, list) else {realm}
    result = []
    for r in all_rules:
        if not r.get("realm"):
            result.append(r)
            continue
        rule_realms = {x.strip() for x in r["realm"].split(",") if x.strip()}
        if rule_realms & allowed:
            result.append(r)
    return result


async def rule_update(
    rule_id: int,
    name: Optional[str] = None,
    attribute_name: Optional[str] = None,
    attribute_condition: Optional[str] = None,
    attribute_value: Optional[str] = None,
    realm: Optional[str] = None,
    activate: Optional[bool] = None,
    admin: Optional[bool] = None,
    deny: Optional[bool] = None,
    assign_to_group: Optional[str] = None,
    owner_domains: Optional[str] = None,
    enabled: Optional[bool] = None,
    user_id: Optional[str] = None,
    clear_group: bool = False,
) -> Optional[dict]:
    """Update an existing attribute rule; explicit null can clear a group."""

    async with get_async_session() as session:
        result = await session.execute(
            select(AttributeRule)
            .where(AttributeRule.id == rule_id)
            .with_for_update()
        )
        rule = result.scalars().first()
        if not rule:
            return None

        if name is not None:
            rule.name = name
        if attribute_name is not None:
            rule.attribute_name = attribute_name
        if attribute_condition is not None:
            rule.attribute_condition = AttributeConditionEnum(attribute_condition)
        if attribute_value is not None:
            rule.attribute_value = attribute_value
        if realm is not None:
            rule.realm = realm
        if activate is not None:
            rule.activate = activate
        if admin is not None:
            rule.admin = admin
        if deny is not None:
            rule.deny = deny
        if assign_to_group is not None or clear_group:
            rule.assign_to_group = assign_to_group
        if owner_domains is not None:
            rule.owner_domains = owner_domains
        if enabled is not None:
            rule.enabled = enabled

        log.info(f"Attribute rule {rule_id} updated by user {user_id}.")
        return rule.as_dict()


async def rule_delete(rule_id: int, user_id: Optional[str] = None) -> bool:
    """Delete an attribute rule by ID."""

    async with get_async_session() as session:
        result = await session.execute(
            select(AttributeRule).where(AttributeRule.id == rule_id)
        )
        rule = result.scalars().first()
        if not rule:
            return False
        await session.delete(rule)
        log.info(f"Attribute rule {rule_id} deleted by user {user_id}.")
        return True


async def evaluate_rules(decoded_jwt: dict, user: dict) -> dict:
    """Use the same pure evaluator as simulation; count real matches only."""
    if user.get("manually_deactivated"):
        return {}
    async with get_async_session() as session:
        rows = await session.execute(select(AttributeRule).where(AttributeRule.enabled == True).order_by(AttributeRule.id))
        rules = [r.as_dict() for r in rows.scalars().all()]
    result = evaluate_provisioning(rules, decoded_jwt, user)
    for match in result["rules"]:
        if match["matched"]:
            record("provision.matched")
            log.info("Provisioning rule %s matched", match["id"])
    return result["actions"]


async def apply_rule_actions(actions: dict, user: dict) -> None:
    """
    Apply the evaluated rule actions to a user.

    Parameters:
        actions: The actions dict from evaluate_rules.
        user: The user dict (must contain user_id, username).
    """

    if not actions:
        return

    user_id = user.get("user_id", "")
    username = user.get("username", "")

    async with get_async_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        db_user = result.scalars().first()
        if not db_user:
            return

        changed = False
        if actions.get("deny"):
            changed = db_user.active
            log.info(f"Deny rule matched for user {user_id}, deactivating.")
            db_user.active = False
            await session.commit()
            if changed:
                record("provision.changed")
            return

        if actions.get("activate") and not db_user.active:
            log.info(f"Auto-activating user {user_id} via attribute rule.")
            db_user.active = True
            changed = True

        if actions.get("admin") and not db_user.admin:
            log.info(f"Auto-granting admin to user {user_id} via attribute rule.")
            db_user.admin = True
            changed = True

    if changed:
        record("provision.changed")

    # Group assignment — only if the user is not already in any group
    group_id = actions.get("group")
    if group_id:
        async with get_async_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            db_user_for_group = result.scalars().first()

            existing = None
            if db_user_for_group:
                result = await session.execute(
                    select(GroupUserLink).where(
                        GroupUserLink.user_id == db_user_for_group.id
                    )
                )
                existing = result.scalars().first()

        if existing:
            log.info(
                f"User {user_id} already in group {existing.group_id}, "
                f"skipping rule-based group assignment to {group_id}."
            )
        else:
            try:
                async with get_async_session() as session:
                    result = await session.execute(
                        select(Group).where(Group.id == int(group_id))
                    )
                    group_exists = result.scalars().first()

                if not group_exists:
                    log.warning(
                        f"Rule references group {group_id} which no longer exists, "
                        f"skipping group assignment for user {user_id}."
                    )
                else:
                    await group_add_user(int(group_id), username, provisioning=True)
            except (ValueError, TypeError):
                log.warning(
                    f"Could not assign user {user_id} to group {group_id}."
                )


def _user_to_pseudo_jwt(user: User) -> dict:
    """Build a pseudo-JWT dict from stored user fields for rule testing."""
    username = user.username or ""
    domain = username.split("@")[-1] if "@" in username else ""
    return {
        "preferred_username": username,
        "email": user.email or "",
        "realm": user.realm or "",
        "domain": domain,
    }


async def test_rules(rule_ids: list[int], realm: str | list[str] | None = None) -> list[dict]:
    """
    Test which users would be matched by the given rules.

    Returns a list of dicts with user info and which rules matched.
    """
    async with get_async_session() as session:
        result = await session.execute(
            select(AttributeRule).where(AttributeRule.id.in_(rule_ids))
        )
        rules = result.scalars().all()
        if not rules:
            return []

        # Build group ID → name map for display
        group_ids = set()
        for r in rules:
            if r.assign_to_group:
                try:
                    group_ids.add(int(r.assign_to_group))
                except (ValueError, TypeError):
                    pass
        group_names = {}
        if group_ids:
            groups_result = await session.execute(
                select(Group).where(Group.id.in_(group_ids))
            )
            groups = groups_result.scalars().all()
            group_names = {str(g.id): g.name for g in groups}

        stmt = select(User).where(
            User.deleted == False,  # noqa: E712
            User.username != "api_user",
        )
        if realm and realm != "*":
            if isinstance(realm, list):
                stmt = stmt.where(User.realm.in_(realm))
            else:
                stmt = stmt.where(User.realm == realm)

        users_result = await session.execute(stmt)
        users = users_result.scalars().all()
        results = []

        for user in users:
            pseudo_jwt = _user_to_pseudo_jwt(user)
            matched_rules = []

            for rule in rules:
                if rule.realm:
                    rule_realms = [r.strip() for r in rule.realm.split(",") if r.strip()]
                    if rule_realms and user.realm not in rule_realms:
                        continue

                claim_values = _get_claim_values(pseudo_jwt, rule.attribute_name)
                if not claim_values:
                    continue

                matched = any(
                    _match_condition(rule.attribute_condition, cv, rule.attribute_value)
                    for cv in claim_values
                )
                if matched:
                    rule_info = {"id": rule.id, "name": rule.name}
                    actions = []
                    if rule.activate:
                        actions.append("Activate")
                    if rule.deny:
                        actions.append("Deny")
                    if rule.admin:
                        actions.append("Grant admin")
                    if rule.assign_to_group:
                        gname = group_names.get(rule.assign_to_group, rule.assign_to_group)
                        actions.append(f"Group: {gname}")
                    rule_info["actions"] = actions
                    matched_rules.append(rule_info)

            if matched_rules:
                results.append({
                    "username": user.username,
                    "email": user.email or "",
                    "realm": user.realm,
                    "active": user.active,
                    "admin": user.admin,
                    "matched_rules": matched_rules,
                })

        return results
