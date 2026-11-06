import unittest
from datetime import datetime, timedelta, timezone

from ps_internet.status import (
    desired_enabled,
    explain,
    grade_band,
    grades_allow_internet,
    is_problem,
    quarter_grades,
    restore_after_override,
    rules_allow_internet,
    rules_for_grades,
    rules_for_override,
    unexcused_problems,
)


class StatusTests(unittest.TestCase):
    def test_problem_is_missing_or_under_cutoff(self):
        self.assertTrue(is_problem({"missing": True, "percent": None, "exempt": False}, 80))
        self.assertTrue(is_problem({"missing": False, "percent": 79.9, "exempt": False}, 80))
        self.assertFalse(is_problem({"missing": False, "percent": 80, "exempt": False}, 80))
        self.assertFalse(is_problem({"missing": False, "percent": None, "exempt": False}, 80))
        self.assertFalse(is_problem({"missing": True, "percent": 0, "exempt": True}, 80))

    def test_excuse_removes_the_assignment_from_the_count(self):
        rows = [
            {"assignment_id": "1", "course": "History", "name": "Outline", "due_date": "2026-11-02", "missing": True, "percent": 0, "exempt": False},
            {"assignment_id": "2", "course": "Math", "name": "Quiz", "due_date": "2026-11-03", "missing": False, "percent": 70, "exempt": False},
        ]
        kept = unexcused_problems(rows, {"1"}, 80)
        self.assertEqual([row["assignment_id"] for row in kept], ["2"])

    def test_rule_direction(self):
        self.assertTrue(desired_enabled("enable", True))
        self.assertFalse(desired_enabled("enable", False))
        self.assertFalse(desired_enabled("disable", True))
        self.assertTrue(desired_enabled("disable", False))

    def test_internet_on_when_each_rule_is_in_the_allow_position(self):
        rules = [
            {"use_rules": True, "present": True, "enabled": True, "when_internet": "enable"},
            {"use_rules": True, "present": True, "enabled": False, "when_internet": "disable"},
            {"use_rules": False, "use_override": True, "present": True, "enabled": False, "when_internet": "enable"},
        ]
        self.assertTrue(rules_allow_internet(rules))
        rules[1]["enabled"] = True
        self.assertFalse(rules_allow_internet(rules))
        self.assertIsNone(rules_allow_internet([]))

    def test_a_rule_can_be_automatic_override_or_both(self):
        rules = [
            {"policy_id": "auto", "use_rules": True, "use_override": False, "present": True},
            {"policy_id": "over", "use_rules": False, "use_override": True, "present": True},
            {"policy_id": "both", "use_rules": True, "use_override": True, "present": True},
        ]
        self.assertEqual([rule["policy_id"] for rule in rules_for_grades(rules)], ["auto", "both"])
        self.assertEqual(
            [rule["policy_id"] for rule in rules_for_grades(rules, override_active=True)],
            ["auto"],
        )
        self.assertEqual([rule["policy_id"] for rule in rules_for_override(rules)], ["over", "both"])
        saved = {"auto": True, "over": False, "both": True}
        self.assertEqual(restore_after_override(saved, rules), {"over": False})

    def test_public_page_explains_an_off_override_and_lists_problems(self):
        now = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)
        view = explain(
            configured=True,
            internet_on=True,
            override={"active": True, "internet_on": False, "until_at": (now + timedelta(hours=2)).isoformat()},
            problems=[{"name": "Outline"}],
            now=now,
        )
        self.assertEqual(view["state"], "off")
        self.assertIn("parent", view["why"])
        self.assertEqual(view["problems"], [{"name": "Outline"}])

    def test_internet_follows_assignments_and_not_the_quarter_grade(self):
        missing = [{"assignment_id": "1", "missing": True}]
        self.assertFalse(grades_allow_internet(missing))
        self.assertTrue(grades_allow_internet([]))

    def test_quarter_colors(self):
        self.assertEqual(grade_band(85), "green")
        self.assertEqual(grade_band(84), "yellow")
        self.assertEqual(grade_band(80), "yellow")
        self.assertEqual(grade_band(79), "red")
        self.assertIsNone(grade_band(None))
        rows = quarter_grades(
            [
                {"period": "2", "name": "History", "store_code": "Q2", "grade": "D 65"},
                {"period": "6", "name": "Wood", "store_code": "Y1", "grade": "A 92"},
                {"period": "1", "name": "French", "store_code": "Q2", "grade": None},
                {"period": "3", "name": "Finance", "store_code": "Q2", "grade": "B 88"},
            ]
        )
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["History"]["band"], "red")
        self.assertEqual(by_name["Finance"]["band"], "green")
        self.assertIsNone(by_name["French"]["band"])
        self.assertIsNone(by_name["Wood"]["grade"])
        self.assertEqual(by_name["Wood"]["store_code"], "")

    def test_off_without_override_uses_the_assignments_as_the_reason(self):
        view = explain(
            configured=True,
            internet_on=False,
            override={"active": False},
            problems=[{"name": "Outline"}],
            now=datetime(2026, 11, 6, tzinfo=timezone.utc),
        )
        self.assertEqual(view["state"], "off")
        self.assertIn("missing", view["why"])


if __name__ == "__main__":
    unittest.main()
