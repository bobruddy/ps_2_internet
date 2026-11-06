"""Decide which assignments count, and whether internet should be on."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/New_York"
EASTERN = ZoneInfo(DEFAULT_TIMEZONE)
TIMEZONES = (
    ("America/New_York", "US/Eastern"),
    ("America/Chicago", "US/Central"),
    ("America/Denver", "US/Mountain"),
    ("America/Phoenix", "US/Arizona"),
    ("America/Los_Angeles", "US/Pacific"),
    ("America/Anchorage", "US/Alaska"),
    ("Pacific/Honolulu", "US/Hawaii"),
    ("UTC", "UTC"),
)


def course_percent(grade: str | None) -> int | None:
    if not grade:
        return None
    token = grade.split()[-1]
    return int(token) if token.isdigit() else None


def is_problem(assignment: dict, threshold: float) -> bool:
    if assignment.get("exempt"):
        return False
    if assignment.get("missing"):
        return True
    percent = assignment.get("percent")
    if percent is None:
        return False
    return float(percent) < threshold


def assignment_rows(assignments: list[dict], excused_ids: set[str], threshold: float) -> list[dict]:
    """Every assignment, newest due date first. A blank date stays at the bottom."""
    rows = []
    for item in assignments:
        percent = item.get("percent")
        number = None if percent is None else float(percent)
        counts = is_problem(item, threshold)
        if item.get("exempt"):
            band = None
        elif item.get("missing"):
            band = "red"
        else:
            band = grade_band(number)
        rows.append(
            {
                **item,
                "band": band,
                "counts": counts,
                "excused": item["assignment_id"] in excused_ids,
            }
        )
    rows.sort(key=lambda item: (item.get("course") or "", item.get("name") or ""))
    rows.sort(key=lambda item: item.get("due_date") or "0000-00-00", reverse=True)
    return rows


def grade_band(percent: float | None) -> str | None:
    """Green above 84, yellow above 79, red below 80. A missing grade has no band."""
    if percent is None:
        return None
    if percent > 84:
        return "green"
    if percent > 79:
        return "yellow"
    if percent < 80:
        return "red"
    return None


def quarter_grades(courses: list[dict], with_assignments: set[str] | None = None) -> list[dict]:
    """Latest quarter marks for display. Year grades are left out.

    When with_assignments is set, classes with no assignment this year are omitted.
    """
    rows = []
    for course in courses:
        name = course.get("name") or ""
        if with_assignments is not None and name not in with_assignments:
            continue
        code = str(course.get("store_code") or "")
        grade = course.get("grade") if code.upper().startswith("Q") else None
        shown_code = code if code.upper().startswith("Q") else ""
        percent = course_percent(grade)
        rows.append(
            {
                "period": course.get("period") or "",
                "name": name,
                "store_code": shown_code,
                "grade": grade,
                "percent": percent,
                "band": grade_band(percent),
            }
        )
    rows.sort(key=lambda item: (item["period"], item["name"]))
    return rows


def grades_allow_internet(problems: list[dict]) -> bool:
    """Internet follows assignments only. Quarter grades are display."""
    return not problems


def unexcused_problems(assignments: list[dict], excused_ids: set[str], threshold: float) -> list[dict]:
    problems = []
    for assignment in assignments:
        if assignment["assignment_id"] in excused_ids:
            continue
        if is_problem(assignment, threshold):
            problems.append(assignment)
    problems.sort(key=lambda item: (item.get("due_date") or "", item.get("course") or "", item.get("name") or ""))
    return problems


def desired_enabled(when_internet: str, internet_on: bool) -> bool:
    """when_internet says what to do with the rule when internet is allowed."""
    if when_internet == "enable":
        return internet_on
    if when_internet == "disable":
        return not internet_on
    raise ValueError(f"unknown rule direction {when_internet!r}")


def rules_for_grades(rules: list[dict], *, override_active: bool = False) -> list[dict]:
    """Automatic rules. While an override is active, leave the ones it also owns alone."""
    chosen = [rule for rule in rules if rule.get("use_rules") and rule.get("present", True)]
    if override_active:
        chosen = [rule for rule in chosen if not rule.get("use_override")]
    return chosen


def rules_for_override(rules: list[dict]) -> list[dict]:
    return [rule for rule in rules if rule.get("use_override") and rule.get("present", True)]


def restore_after_override(restore: dict, rules: list[dict]) -> dict:
    """Put override-only rules back. Grade-managed rules follow the grades instead."""
    automatic = {rule["policy_id"] for rule in rules if rule.get("use_rules")}
    return {policy_id: enabled for policy_id, enabled in restore.items() if policy_id not in automatic}


def rules_allow_internet(rules: list[dict]) -> bool | None:
    scoped = rules_for_grades(rules)
    if not scoped:
        return None
    return all(bool(rule.get("enabled")) == desired_enabled(rule["when_internet"], True) for rule in scoped)


def explain(
    *,
    configured: bool,
    internet_on: bool | None,
    override: dict | None,
    problems: list[dict],
    now: datetime,
    zone: str | None = None,
) -> dict:
    active = _active_override(override, now)
    if active is not None:
        on = bool(active["internet_on"])
        until = format_local(active["until_at"], zone)
        verb = "on" if on else "off"
        return {
            "state": "on" if on else "off",
            "headline": f"Internet is {verb}.",
            "why": f"A parent turned it {verb} until {until}.",
            "problems": problems,
            "show_problems": bool(problems),
        }
    if not configured or internet_on is None:
        return {
            "state": "unconfigured",
            "headline": "Internet is not being controlled from this page.",
            "why": "No rules are set to follow grades yet.",
            "problems": problems,
            "show_problems": bool(problems),
        }
    if internet_on:
        return {
            "state": "on",
            "headline": "Internet is on.",
            "why": "",
            "problems": problems,
            "show_problems": bool(problems),
        }
    why = "An assignment is missing or under the cutoff, and it is not excused."
    if not problems:
        why = "The automatic rules are not in the internet-on position."
    return {
        "state": "off",
        "headline": "Internet is off.",
        "why": why,
        "problems": problems,
        "show_problems": bool(problems),
    }


def _active_override(override: dict | None, now: datetime) -> dict | None:
    if not override or not override.get("active") or not override.get("until_at"):
        return None
    until = datetime.fromisoformat(override["until_at"])
    if until.tzinfo is None:
        until = until.replace(tzinfo=ZoneInfo("UTC"))
    current = now if now.tzinfo else now.replace(tzinfo=ZoneInfo("UTC"))
    if until <= current:
        return None
    return override


def zone_for(name: str | None) -> ZoneInfo:
    wanted = name or DEFAULT_TIMEZONE
    if wanted not in {key for key, _label in TIMEZONES}:
        wanted = DEFAULT_TIMEZONE
    return ZoneInfo(wanted)


def format_local(value: str | None, zone_name: str | None = None) -> str:
    if not value:
        return ""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(zone_for(zone_name)).strftime("%b %-d, %-I:%M %p %Z")
