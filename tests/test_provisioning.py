"""Provisioning permission boundaries, simulation parity and explicit clears."""
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from db import attribute_rules as db
from db.models import AttributeRule
from routers import rules as api
from utils.provisioning import evaluate_provisioning, match_condition, summarize_provisioning


def rule(**changes):
    return dict(dict(id=1, name="Staff", realm="example.org", enabled=True,
                     attribute_name="role", attribute_condition="EQUALS", attribute_value="staff",
                     activate=True, deny=False, admin=False, assign_to_group=None), **changes)


class DecisionTests(unittest.TestCase):
    def test_all_conditions_and_invalid_regex(self):
        for condition, actual, expected in [
            ("EQUALS", "staff", "staff"), ("NOT_EQUALS", "staff", "student"),
            ("CONTAINS", "staff-member", "staff"), ("NOT_CONTAINS", "staff", "student"),
            ("STARTS_WITH", "staff-member", "staff"), ("ENDS_WITH", "staff-member", "member"),
            ("REGEX_MATCH", "staff", "^staff$"),
        ]:
            self.assertTrue(match_condition(condition, actual, expected))
            self.assertTrue(match_condition(condition.lower(), actual, expected))
        self.assertFalse(match_condition("REGEX_MATCH", "staff", "["))

    def test_scope_disabled_arrays_and_missing_attributes(self):
        user = {"realm": "example.org"}
        for claims, expected in [({"role": ["student", "staff"]}, True), ({"role":"student,staff"}, False), ({}, False)]:
            result = evaluate_provisioning([rule(), rule(id=2, realm="other.org"), rule(id=3, enabled=False)], claims, user)
            self.assertEqual(result["actions"]["activate"], expected)
            self.assertEqual(len(result["rules"]), 2)
            self.assertFalse(result["rules"][1]["matched"])

    def test_derived_values_global_scope_and_order(self):
        rules = [rule(id=2, realm=None, attribute_name="domain", attribute_value="example.org", assign_to_group="2"),
                 rule(id=1, attribute_name="realm", attribute_value="example.org", assign_to_group="1")]
        result = evaluate_provisioning(rules, {}, {"realm":"example.org", "username":"person@example.org"})
        self.assertEqual(result["actions"]["group"], "2")
        self.assertEqual([r["id"] for r in result["rules"]], [1,2])

    def test_manual_overrides_and_deny_precedence(self):
        r = rule(deny=True, admin=True, assign_to_group="1")
        for flag in ("manually_activated", "manually_deactivated"):
            user = {"realm":"example.org", flag:True}
            result = evaluate_provisioning([r], {"role":"staff"}, user)
            self.assertFalse(result["actions"]["deny"])
            self.assertIsNone(result["actions"]["group"])
            if flag == "manually_deactivated":
                self.assertFalse(result["rules"][0]["matched"])
        actions = evaluate_provisioning([r], {"role":"staff"}, {"realm":"example.org"})["actions"]
        summary = summarize_provisioning(actions, {}, False, "Staff")
        self.assertEqual(summary, ["Deactivate account. No admin or group changes will be applied."])
        actions["deny"] = False
        self.assertIn("Keep existing group; group assignment is skipped.", summarize_provisioning(actions, {}, True, "Staff"))
        self.assertIn("Group assignment skipped: target group no longer exists.", summarize_provisioning(actions, {}, False, None))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.admin = {"user_id":"admin", "realm":"example.org", "admin_domains":"second.org", "bofh":False}
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.app.dependency_overrides[api.get_current_admin_user] = lambda:self.admin
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def test_forbid_edit_and_delete_global_shared_foreign_rules(self):
        for realm in (None, "", "example.org,other.org", "other.org"):
            with patch.object(api, "rule_get", AsyncMock(return_value=rule(realm=realm))), patch.object(api, "rule_update", AsyncMock()) as update, patch.object(api, "rule_delete", AsyncMock()) as delete:
                self.assertEqual(self.client.put("/admin/rules/1", json={"enabled":False}).status_code,403)
                self.assertEqual(self.client.delete("/admin/rules/1").status_code,403)
                update.assert_not_called()
                delete.assert_not_called()

    def test_cannot_widen_scope_to_global_or_foreign(self):
        for realm in (None, "", " , ", "other.org", "example.org,other.org"):
            with patch.object(api, "rule_get", AsyncMock(return_value=rule())), patch.object(api, "rule_update", AsyncMock()) as update:
                self.assertEqual(self.client.put("/admin/rules/1", json={"realm":realm}).status_code,403)
                update.assert_not_called()
            self.assertEqual(self.client.post("/admin/rules", json=dict(name="test", attribute_name="role", attribute_condition="EQUALS", attribute_value="staff", realm=realm)).status_code,403)

    def test_owned_multirealm_create_and_bofh_global_edit(self):
        with patch.object(api, "rule_create", AsyncMock(return_value=rule())) as create:
            response=self.client.post("/admin/rules",json=dict(name="test",attribute_name="role",attribute_condition="EQUALS",attribute_value="staff",realm="example.org,second.org"))
            self.assertEqual(response.status_code,200)
            self.assertEqual(create.call_args.kwargs["realm"],"example.org,second.org")
        self.admin["bofh"] = True
        with patch.object(api,"rule_update",AsyncMock(return_value=rule(realm=None))):
            self.assertEqual(self.client.put("/admin/rules/1",json={"enabled":False}).status_code,200)

    def test_list_is_read_only_when_scope_is_shared(self):
        with patch.object(api,"rule_get_all",AsyncMock(return_value=[rule(),rule(id=2,realm=None),rule(id=3,realm="example.org,other.org")])):
            self.assertEqual([r["can_manage"] for r in self.client.get("/admin/rules").json()["result"]],[True,False,False])

    def test_explicit_null_clears_but_omission_preserves_group(self):
        with patch.object(api,"rule_get",AsyncMock(return_value=rule())), patch.object(api,"rule_update",AsyncMock(return_value=rule())) as update:
            self.assertEqual(self.client.put("/admin/rules/1",json={"assign_to_group":None}).status_code,200)
            self.assertTrue(update.call_args.kwargs["clear_group"])
            self.assertEqual(self.client.put("/admin/rules/1",json={"enabled":False}).status_code,200)
            self.assertFalse(update.call_args.kwargs["clear_group"])

    def test_reject_inaccessible_group(self):
        with patch.object(api,"rule_get",AsyncMock(return_value=rule())), patch.object(api,"group_get",AsyncMock(return_value={})), patch.object(api,"rule_update",AsyncMock()) as update:
            self.assertEqual(self.client.put("/admin/rules/1",json={"assign_to_group":"99"}).status_code,403)
            update.assert_not_called()

    def test_match_uses_backend_conditions_and_enforces_read_access(self):
        with patch.object(api,"rule_get",AsyncMock(return_value=rule(enabled=False))):
            self.assertEqual(self.client.post("/admin/rules/1/match",json={"value":["student","staff"]}).json(),{"matched":True})
        with patch.object(api,"rule_get",AsyncMock(return_value=rule(realm="other.org"))):
            self.assertEqual(self.client.post("/admin/rules/1/match",json={"value":"staff"}).status_code,403)

    def test_simulation_is_read_only_and_realm_limited(self):
        with patch.object(api,"rule_get_all",AsyncMock(return_value=[rule(assign_to_group="1")])), patch.object(api,"group_get",AsyncMock(return_value={"name":"Staff"})), patch.object(db,"apply_rule_actions",AsyncMock()) as apply, patch.object(db,"record") as record:
            payload={"realm":"example.org","attributes":{"role":"staff"},"has_group":True}
            response=self.client.post("/admin/rules/simulate",json=payload)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()["rules"][0]["matched"])
            self.assertIn("Keep existing group; group assignment is skipped.",response.json()["summary"])
            payload["realm"]="other.org"
            self.assertEqual(self.client.post("/admin/rules/simulate",json=payload).status_code,403)
            apply.assert_not_called()
            record.assert_not_called()


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_group_persists_and_other_updates_preserve_it(self):
        engine=create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(AttributeRule.__table__.create)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        @asynccontextmanager
        async def session():
            async with factory() as s:
                yield s
                await s.commit()
        try:
            with patch.object(db,"get_async_session",session):
                created=await db.rule_create(name="test",attribute_name="role",attribute_condition="EQUALS",attribute_value="staff",assign_to_group="7")
                await db.rule_update(created["id"],enabled=False)
                self.assertEqual((await db.rule_get(created["id"]))["assign_to_group"],"7")
                await db.rule_update(created["id"],clear_group=True)
                self.assertIsNone((await db.rule_get(created["id"]))["assign_to_group"])
        finally:
            await engine.dispose()

    async def test_live_login_matches_simulation_and_only_live_counts(self):
        rules=[rule(),rule(id=2,assign_to_group="2"),rule(id=3,realm="elsewhere.org")]
        result=SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[SimpleNamespace(as_dict=lambda r=r:r) for r in rules]))
        @asynccontextmanager
        async def session():
            yield SimpleNamespace(execute=AsyncMock(return_value=result))
        user={"realm":"example.org"}
        with patch.object(db,"get_async_session",session), patch.object(db,"record") as record:
            self.assertEqual(await db.evaluate_rules({"role":"staff"},user),evaluate_provisioning(rules,{"role":"staff"},user)["actions"])
            self.assertEqual(record.call_count,2)
            user["manually_deactivated"]=True
            self.assertEqual(await db.evaluate_rules({"role":"staff"},user),{})
            self.assertEqual(record.call_count,2)
