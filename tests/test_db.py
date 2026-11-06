import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ps_internet.db import list_rules, observe_rules, scope_uptime_rules_once, set_rule_scope


class RuleUseTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("APP_DB")
        self._dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB"] = str(Path(self._dir.name) / "app.db")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APP_DB", None)
        else:
            os.environ["APP_DB"] = self._old
        self._dir.cleanup()

    def test_an_old_in_scope_flag_becomes_both_uses(self):
        path = os.environ["APP_DB"]
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE rules (
                  policy_id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  action TEXT NOT NULL DEFAULT '',
                  enabled INTEGER,
                  in_scope INTEGER NOT NULL DEFAULT 0,
                  when_internet TEXT NOT NULL DEFAULT 'enable',
                  present INTEGER NOT NULL DEFAULT 1,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO rules (policy_id, name, enabled, in_scope, updated_at)
                VALUES ('fast', 'student_uptime_morning', 1, 1, 't'), ('slow', 'student_basespeed', 1, 0, 't')
                """
            )
        rows = {rule["policy_id"]: rule for rule in list_rules()}
        self.assertTrue(rows["fast"]["use_rules"])
        self.assertTrue(rows["fast"]["use_override"])
        self.assertFalse(rows["slow"]["use_rules"])
        self.assertFalse(rows["slow"]["use_override"])

    def test_discover_does_not_change_a_saved_choice(self):
        observe_rules([{"id": "1", "name": "student_uptime_morning", "enabled": True, "description": "07:00–09:00"}])
        scope_uptime_rules_once()
        set_rule_scope("1", False, True, "disable")
        observe_rules([{"id": "1", "name": "student_uptime_morning", "enabled": False, "description": "07:00–09:00"}])
        scope_uptime_rules_once()
        rule = list_rules()[0]
        self.assertFalse(rule["use_rules"])
        self.assertTrue(rule["use_override"])
        self.assertEqual(rule["when_internet"], "disable")
        self.assertFalse(rule["enabled"])


if __name__ == "__main__":
    unittest.main()
