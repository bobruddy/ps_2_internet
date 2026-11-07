import unittest
from datetime import date, datetime, timedelta, timezone

from ps_internet.status import (
    assignment_rows,
    desired_enabled,
    explain,
    format_local,
    grade_band,
    grades_allow_internet,
    grace_ends_on,
    in_grace,
    is_problem,
    public_problem_rows,
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

    def test_five_business_days_still_covers_sunday_after_a_monday_due_date(self):
        monday = "2026-11-02"
        sunday = date(2026, 11, 8)
        next_monday = date(2026, 11, 9)
        self.assertTrue(in_grace(monday, sunday, 5))
        self.assertFalse(in_grace(monday, next_monday, 5))
        self.assertTrue(in_grace("2026-11-06", sunday, 5))
        self.assertFalse(in_grace(monday, sunday, 0))
        self.assertFalse(in_grace(None, sunday, 5))
        row = [{"assignment_id": "1", "course": "History", "name": "Quiz", "due_date": monday, "missing": True, "percent": None, "exempt": False}]
        self.assertEqual(unexcused_problems(row, set(), 80, grace_days=5, today=sunday), [])
        self.assertEqual(
            [item["assignment_id"] for item in unexcused_problems(row, set(), 80, grace_days=5, today=next_monday)],
            ["1"],
        )
        shown = assignment_rows(row, set(), 80, grace_days=5, today=sunday)[0]
        self.assertTrue(shown["in_grace"])
        self.assertTrue(shown["counts"])
        self.assertEqual(shown["band"], "red")
        self.assertEqual(grace_ends_on(monday, 5), next_monday)
        self.assertEqual(shown["grace_until"], "Nov 9")
        self.assertIsNone(grace_ends_on(monday, 0))
        self.assertIsNone(grace_ends_on(None, 5))
        fallen = assignment_rows(row, set(), 80, grace_days=5, today=next_monday)[0]
        self.assertEqual(fallen["grace_until"], "")

    def test_the_public_list_keeps_grace_and_timed_excuses(self):
        rows = [
            {"assignment_id": "old", "course": "History", "name": "Old", "due_date": "2026-09-01", "missing": True, "percent": None, "exempt": False},
            {"assignment_id": "new", "course": "History", "name": "New", "due_date": "2026-11-02", "missing": True, "percent": None, "exempt": False},
            {"assignment_id": "held", "course": "Math", "name": "Quiz", "due_date": "2026-09-02", "missing": False, "percent": 70, "exempt": False},
            {"assignment_id": "forever", "course": "French", "name": "Essay", "due_date": "2026-09-03", "missing": True, "percent": None, "exempt": False},
            {"assignment_id": "fine", "course": "French", "name": "Drill", "due_date": "2026-11-01", "missing": False, "percent": 95, "exempt": False},
        ]
        shown = public_problem_rows(
            rows,
            {"held": "2026-11-09T20:00:00+00:00", "forever": None},
            80,
            grace_days=5,
            today=date(2026, 11, 8),
        )
        by_id = {item["assignment_id"]: item for item in shown}
        self.assertEqual(set(by_id), {"old", "new", "held"})
        self.assertFalse(by_id["old"]["in_grace"])
        self.assertTrue(by_id["new"]["in_grace"])
        self.assertEqual(by_id["new"]["grace_until"], "Nov 9")
        self.assertEqual(by_id["old"]["grace_until"], "")
        self.assertEqual(by_id["old"]["band"], "red")
        self.assertEqual(by_id["new"]["band"], "red")
        self.assertEqual(by_id["held"]["excuse_until"], "2026-11-09T20:00:00+00:00")

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
        self.assertTrue(view["show_problems"])

    def test_an_on_override_still_lists_unexcused_problems(self):
        now = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)
        view = explain(
            configured=True,
            internet_on=False,
            override={"active": True, "internet_on": True, "until_at": (now + timedelta(hours=2)).isoformat()},
            problems=[{"name": "Outline"}],
            now=now,
        )
        self.assertEqual(view["state"], "on")
        self.assertIn("parent", view["why"])
        self.assertEqual(view["problems"], [{"name": "Outline"}])
        self.assertTrue(view["show_problems"])

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
        shown = quarter_grades(
            [
                {"period": "1", "name": "French", "store_code": "Q2", "grade": None},
                {"period": "2", "name": "History", "store_code": "Q2", "grade": "D 65"},
                {"period": "8", "name": "Homeroom", "store_code": "Q2", "grade": None},
            ],
            {"History"},
        )
        self.assertEqual([row["name"] for row in shown], ["History"])

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

    def test_assignments_are_newest_first_and_colored_like_quarters(self):
        rows = assignment_rows(
            [
                {"assignment_id": "old", "course": "History", "name": "Old", "due_date": "2026-08-31", "missing": False, "percent": 70, "exempt": False},
                {"assignment_id": "new", "course": "History", "name": "New", "due_date": "2026-11-05", "missing": False, "percent": 90, "exempt": False},
                {"assignment_id": "mid", "course": "Math", "name": "Quiz", "due_date": "2026-11-02", "missing": True, "percent": None, "exempt": False},
                {"assignment_id": "blank", "course": "French", "name": "Essay", "due_date": None, "missing": False, "percent": 82, "exempt": False},
                {"assignment_id": "ok", "course": "French", "name": "Drill", "due_date": "2026-11-01", "missing": False, "percent": 100, "exempt": False},
                {"assignment_id": "open", "course": "Math", "name": "Lab", "due_date": "2026-11-06", "missing": False, "percent": None, "exempt": False},
            ],
            {"old"},
            80,
        )
        self.assertEqual([row["assignment_id"] for row in rows], ["open", "new", "mid", "ok", "old", "blank"])
        by_id = {row["assignment_id"]: row for row in rows}
        self.assertIsNone(by_id["open"]["band"])
        self.assertFalse(by_id["open"]["counts"])
        self.assertEqual(by_id["new"]["band"], "green")
        self.assertFalse(by_id["new"]["counts"])
        self.assertEqual(by_id["mid"]["band"], "red")
        self.assertTrue(by_id["mid"]["counts"])
        self.assertEqual(by_id["blank"]["band"], "yellow")
        self.assertFalse(by_id["blank"]["counts"])
        self.assertEqual(by_id["old"]["band"], "red")
        self.assertTrue(by_id["old"]["excused"])
        self.assertTrue(by_id["old"]["counts"])

    def test_displayed_times_use_the_chosen_zone(self):
        stored = "2026-11-06T16:04:00+00:00"
        self.assertEqual(format_local(stored, "America/New_York"), "Nov 6, 11:04 AM EST")
        self.assertEqual(format_local(stored, "America/Los_Angeles"), "Nov 6, 8:04 AM PST")
        self.assertEqual(format_local(stored, "not-a-zone"), "Nov 6, 11:04 AM EST")
        now = datetime(2026, 11, 6, 16, tzinfo=timezone.utc)
        view = explain(
            configured=True,
            internet_on=False,
            override={"active": True, "internet_on": True, "until_at": "2026-11-06T18:04:00+00:00"},
            problems=[],
            now=now,
            zone="America/Chicago",
        )
        self.assertIn("12:04 PM CST", view["why"])


if __name__ == "__main__":
    unittest.main()
