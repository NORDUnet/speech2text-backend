"""Pure provisioning decisions shared by login and read-only simulation."""
import re


def claim_values(claims: dict, name: str) -> list[str]:
    value = claims.get(name)
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def match_condition(condition: str, actual: str, expected: str) -> bool:
    condition = condition.upper()
    if condition == "EQUALS":
        return actual == expected
    if condition == "NOT_EQUALS":
        return actual != expected
    if condition == "CONTAINS":
        return expected in actual
    if condition == "NOT_CONTAINS":
        return expected not in actual
    if condition == "STARTS_WITH":
        return actual.startswith(expected)
    if condition == "ENDS_WITH":
        return actual.endswith(expected)
    if condition == "REGEX_MATCH":
        try:
            return bool(re.search(expected, actual))
        except re.error:
            return False
    return False


def evaluate_provisioning(rules: list[dict], claims: dict, user: dict) -> dict:
    realm = user.get("realm", "")
    username = user.get("username", "")
    claims = dict(claims)
    claims.setdefault("domain", username.split("@")[-1] if "@" in username else "")
    claims.setdefault("realm", realm)
    actions = {"activate": False, "admin": False, "deny": False, "group": None}
    results = []
    for rule in sorted(rules, key=lambda r: r["id"]):
        if not rule.get("enabled"):
            continue
        scopes = {r.strip() for r in (rule.get("realm") or "").split(",") if r.strip()}
        values = claim_values(claims, rule["attribute_name"])
        reason = "Matched"
        if user.get("manually_deactivated"):
            reason = "Skipped: account manually deactivated"
        elif scopes and realm not in scopes:
            reason = "Skipped: different realm"
        elif not values:
            reason = "Attribute not supplied"
        elif not any(match_condition(rule["attribute_condition"], v, rule["attribute_value"]) for v in values):
            reason = "No match"
        matched = reason == "Matched"
        results.append({"id": rule["id"], "name": rule["name"], "matched": matched, "reason": reason})
        if not matched:
            continue
        for key in ("activate", "admin"):
            actions[key] |= bool(rule.get(key))
        if not user.get("manually_activated"):
            actions["deny"] |= bool(rule.get("deny"))
            if rule.get("assign_to_group"):
                actions["group"] = rule["assign_to_group"]
    return {"actions": actions, "rules": results}


def summarize_provisioning(actions: dict, user: dict, has_group: bool, group_name: str | None) -> list[str]:
    if user.get("manually_deactivated"):
        return ["No changes: account is manually deactivated."]
    if actions.get("deny"):
        return ["Deactivate account. No admin or group changes will be applied."]
    messages = []
    if user.get("manually_activated"):
        messages.append("Manual activation preserved; deactivation and group assignment are ignored.")
    if actions.get("activate"):
        messages.append("Activate account if it is not already active.")
    if actions.get("admin"):
        messages.append("Grant administrator access if not already granted.")
    if actions.get("group"):
        if has_group:
            messages.append("Keep existing group; group assignment is skipped.")
        elif group_name:
            messages.append(f"Assign to group: {group_name}.")
        else:
            messages.append("Group assignment skipped: target group no longer exists.")
    return messages or ["No account changes."]
