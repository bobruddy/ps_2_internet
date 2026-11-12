import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ps_internet.db import (
    LEGACY_STUDENT_ID,
    clear_expired_excuses,
    connect,
    ensure_student,
    excused_ids,
    list_rules,
    list_students,
    observe_rules,
    scope_uptime_rules_once,
    set_excuse,
    set_rule_scope,
)


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
        rows = {rule["policy_id"]: rule for rule in list_rules(LEGACY_STUDENT_ID)}
        self.assertTrue(rows["fast"]["use_rules"])
        self.assertTrue(rows["fast"]["use_override"])
        self.assertFalse(rows["slow"]["use_rules"])
        self.assertFalse(rows["slow"]["use_override"])

    def test_discover_does_not_change_a_saved_choice(self):
        ensure_student("ada", "Ada")
        observe_rules("ada", [{"id": "1", "name": "student_uptime_morning", "enabled": True, "description": "07:00–09:00"}])
        scope_uptime_rules_once("ada")
        set_rule_scope("ada", "1", False, True, "disable")
        observe_rules("ada", [{"id": "1", "name": "student_uptime_morning", "enabled": False, "description": "07:00–09:00"}])
        scope_uptime_rules_once("ada")
        rule = list_rules("ada")[0]
        self.assertFalse(rule["use_rules"])
        self.assertTrue(rule["use_override"])
        self.assertEqual(rule["when_internet"], "disable")
        self.assertFalse(rule["enabled"])

    def test_a_new_gateway_id_keeps_the_rule_with_the_same_name(self):
        ensure_student("ada", "Ada")
        observe_rules(
            "ada",
            [{"id": "old", "name": "student_uptime_morning", "enabled": True, "description": "07:00–09:00"}],
        )
        set_rule_scope("ada", "old", True, False, "disable")
        observe_rules(
            "ada",
            [{"id": "new", "name": "student_uptime_morning", "enabled": False, "description": "08:00–10:00"}],
        )
        rules = list_rules("ada")
        self.assertEqual([rule["policy_id"] for rule in rules], ["new"])
        rule = rules[0]
        self.assertTrue(rule["use_rules"])
        self.assertFalse(rule["use_override"])
        self.assertEqual(rule["when_internet"], "disable")
        self.assertTrue(rule["present"])
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["description"], "08:00–10:00")

    def test_a_copied_rule_row_is_folded_back_onto_the_saved_name(self):
        ensure_student("ada", "Ada")
        observe_rules(
            "ada",
            [{"id": "old", "name": "student_uptime_morning", "enabled": False, "description": "old"}],
        )
        set_rule_scope("ada", "old", True, False, "enable")
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO rules (
                  student_id, policy_id, name, description, action, enabled, in_scope,
                  use_rules, use_override, when_internet, present, updated_at
                )
                VALUES ('ada', 'new', 'student_uptime_morning', 'new', '', 1, 0, 0, 0, 'enable', 1, 't')
                """
            )
        observe_rules(
            "ada",
            [{"id": "new", "name": "student_uptime_morning", "enabled": True, "description": "new"}],
        )
        rules = list_rules("ada")
        self.assertEqual([rule["policy_id"] for rule in rules], ["new"])
        self.assertTrue(rules[0]["use_rules"])
        self.assertFalse(rules[0]["use_override"])
        self.assertTrue(rules[0]["present"])

    def test_a_different_name_stays_a_different_rule(self):
        ensure_student("ada", "Ada")
        observe_rules("ada", [{"id": "old", "name": "student_uptime_morning", "enabled": True, "description": ""}])
        set_rule_scope("ada", "old", True, False, "enable")
        observe_rules("ada", [{"id": "evening", "name": "student_uptime_evening", "enabled": True, "description": ""}])
        rows = {rule["name"]: rule for rule in list_rules("ada")}
        self.assertEqual(rows["student_uptime_morning"]["policy_id"], "old")
        self.assertFalse(rows["student_uptime_morning"]["present"])
        self.assertTrue(rows["student_uptime_morning"]["use_rules"])
        self.assertEqual(rows["student_uptime_evening"]["policy_id"], "evening")
        self.assertTrue(rows["student_uptime_evening"]["present"])
        self.assertFalse(rows["student_uptime_evening"]["use_rules"])

    def test_a_day_excuse_ends_and_a_plain_excuse_does_not(self):
        ensure_student("ada", "Ada")
        set_excuse("ada", "kept", True)
        set_excuse("ada", "soon", True, days=3)
        with connect() as conn:
            conn.execute(
                "UPDATE excuses SET until_at = ? WHERE assignment_id = ?",
                ("2000-01-01T00:00:00+00:00", "soon"),
            )
        self.assertEqual(excused_ids("ada"), {"kept"})
        self.assertEqual(clear_expired_excuses(), ["ada"])
        self.assertEqual(excused_ids("ada"), {"kept"})

    def test_a_new_database_does_not_invent_a_student(self):
        connect().close()
        connect().close()
        ensure_student("ada", "Ada")
        self.assertEqual([row["student_id"] for row in list_students()], ["ada"])


if __name__ == "__main__":
    unittest.main()
