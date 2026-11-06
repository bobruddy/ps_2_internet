import os
import tempfile
import unittest
from pathlib import Path

os.environ["PS_SCHEDULER"] = "0"
os.environ["ADMIN_PASSWORD"] = "test"
os.environ["ADMIN_SESSION_SECRET"] = "test-secret"

from fastapi.testclient import TestClient

from ps_internet.db import excused_ids, list_rules, observe_rules, replace_assignments, set_meta
from ps_internet.web import app


class AdminRuleChoiceTests(unittest.TestCase):
    def setUp(self):
        self._old_db = os.environ.get("APP_DB")
        self._dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB"] = str(Path(self._dir.name) / "app.db")
        observe_rules(
            [
                {"id": "fast", "name": "student_uptime_morning", "enabled": True, "description": "07:00–09:00"},
                {"id": "slow", "name": "student_basespeed", "enabled": True, "description": "ALWAYS"},
            ]
        )
        self.client = TestClient(app)
        login = self.client.get("/admin/login")
        token = login.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        signed_in = self.client.post("/admin/login", data={"password": "test", "csrf": token})
        self.assertEqual(signed_in.status_code, 200)
        self.assertIn("QoS rules", signed_in.text)

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("APP_DB", None)
        else:
            os.environ["APP_DB"] = self._old_db
        self._dir.cleanup()

    def test_each_rule_can_be_rule_based_override_or_both(self):
        page = self.client.get("/admin")
        self.assertIn("Rule based", page.text)
        self.assertIn("Override", page.text)
        self._save("fast", use_rules=True, use_override=False)
        self._save("slow", use_rules=True, use_override=True)
        rows = {rule["policy_id"]: rule for rule in list_rules()}
        self.assertTrue(rows["fast"]["use_rules"])
        self.assertFalse(rows["fast"]["use_override"])
        self.assertTrue(rows["slow"]["use_rules"])
        self.assertTrue(rows["slow"]["use_override"])
        again = self.client.get("/admin")
        fast = again.text.split("student_uptime_morning", 1)[1].split("</form>", 1)[0]
        slow = again.text.split("student_basespeed", 1)[1].split("</form>", 1)[0]
        self.assertIn('name="use_rules" value="1" checked', fast)
        self.assertNotIn('name="use_override" value="1" checked', fast)
        self.assertIn('name="use_rules" value="1" checked', slow)
        self.assertIn('name="use_override" value="1" checked', slow)

    def _save(self, policy_id: str, use_rules: bool, use_override: bool) -> None:
        page = self.client.get("/admin")
        token = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        data = {"csrf": token, "policy_id": policy_id, "when_internet": "enable"}
        if use_rules:
            data["use_rules"] = "1"
        if use_override:
            data["use_override"] = "1"
        saved = self.client.post("/admin/rules", data=data)
        self.assertEqual(saved.status_code, 200)
        self.assertIn("Rule saved", saved.text)

    def test_excuse_and_rule_changes_can_save_without_a_new_page(self):
        set_meta("uptime_scoped", "1")
        replace_assignments(
            [
                {
                    "assignment_id": "a1",
                    "course": "History",
                    "name": "Outline",
                    "due_date": "2026-11-02",
                    "missing": True,
                    "percent": None,
                    "exempt": False,
                }
            ]
        )
        page = self.client.get("/admin")
        token = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        excused = self.client.post(
            "/admin/excuse",
            data={"csrf": token, "assignment_id": "a1", "excused": "1"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(excused.status_code, 200)
        self.assertEqual(excused.json(), {"ok": True})
        self.assertEqual(excused_ids(), {"a1"})
        rule = self.client.post(
            "/admin/rules",
            data={"csrf": token, "policy_id": "slow", "use_override": "1", "when_internet": "disable"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(rule.status_code, 200)
        self.assertTrue(rule.json()["ok"])
        saved = {row["policy_id"]: row for row in list_rules()}["slow"]
        self.assertFalse(saved["use_rules"])
        self.assertTrue(saved["use_override"])
        self.assertEqual(saved["when_internet"], "disable")


if __name__ == "__main__":
    unittest.main()
