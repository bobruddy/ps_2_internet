import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ps_internet.db import ensure_student, list_rules, observe_rules, replace_assignments, set_rule_scope, update_student
from ps_internet.jobs import _verify_managed_rules


class GradeCheckVerifiesRules(unittest.TestCase):
    def setUp(self):
        self._old_db = os.environ.get("APP_DB")
        self._old_key = os.environ.get("UNIFI_API_KEY")
        self._dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB"] = str(Path(self._dir.name) / "app.db")
        os.environ["UNIFI_API_KEY"] = "test-key"
        ensure_student("25144", "Ethan")
        update_student("25144", rule_prefix="ethan", threshold_percent=80, grace_days=0)
        observe_rules(
            "25144",
            [
                {"id": "up", "name": "ethan_uptime_morning", "enabled": False, "description": "07:00–09:00"},
                {"id": "base", "name": "ethan_basespeed", "enabled": True, "description": "ALWAYS"},
            ],
        )
        set_rule_scope("25144", "up", True, False, "enable")
        set_rule_scope("25144", "base", True, True, "disable")
        replace_assignments(
            "25144",
            [
                {
                    "assignment_id": "quiz",
                    "course": "Math",
                    "name": "Quiz",
                    "due_date": "2020-01-06",
                    "missing": True,
                    "exempt": False,
                }
            ],
        )

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("APP_DB", None)
        else:
            os.environ["APP_DB"] = self._old_db
        if self._old_key is None:
            os.environ.pop("UNIFI_API_KEY", None)
        else:
            os.environ["UNIFI_API_KEY"] = self._old_key
        self._dir.cleanup()

    def test_a_grade_check_reads_the_gateway_before_deciding_rules_are_correct(self):
        written = []

        class Gateway:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def matching(self, needle):
                return [
                    {"id": "up", "name": "ethan_uptime_morning", "enabled": False, "description": "07:00–09:00"},
                    {"id": "base", "name": "ethan_basespeed", "enabled": False, "description": "ALWAYS"},
                ]

            def set_enabled(self, policy_id, enabled, name_prefix=""):
                written.append((policy_id, enabled, name_prefix))
                return {}

        with patch("ps_internet.jobs.UnifiClient", Gateway):
            _verify_managed_rules("25144")

        self.assertIn(("base", True, "ethan"), written)
        saved = {rule["policy_id"]: rule["enabled"] for rule in list_rules("25144")}
        self.assertTrue(saved["base"])
        self.assertFalse(saved["up"])
