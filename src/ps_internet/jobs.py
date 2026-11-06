"""PowerSchool sync, QoS pause and resume, and timed internet overrides."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from ps_internet.client import PowerSchoolClient, PowerSchoolError
from ps_internet.config import env
from ps_internet.db import (
    clear_override,
    excused_ids,
    get_meta,
    get_override,
    list_assignments,
    list_rules,
    now,
    observe_rules,
    replace_assignments,
    set_meta,
    set_override,
    set_rule_enabled,
    scope_uptime_rules_once,
    display_timezone,
    threshold,
)
from ps_internet.parse import (
    assignments_from_lookup,
    current_term,
    current_quarter_code,
    latest_quarter,
    grades_disabled,
    parse_courses,
    parse_missing_context,
    parse_scores_meta,
    parse_students,
)
from ps_internet.status import (
    desired_enabled,
    grades_allow_internet,
    zone_for,
    restore_after_override,
    rules_allow_internet,
    rules_for_grades,
    rules_for_override,
    unexcused_problems,
)
from ps_internet.unifi import UnifiClient, UnifiError

log = logging.getLogger("ps_internet")
_lock = threading.Lock()


def grade_sync() -> dict:
    with _lock:
        return _grade_sync()


def refresh_rules() -> list[dict]:
    with _lock:
        known = {rule["policy_id"] for rule in list_rules()}
        needle = (env("UNIFI_RULE_MATCH", "") or "").strip()
        if not needle:
            set_meta("rules_error", "UNIFI_RULE_MATCH is not set")
            return list_rules()
        with UnifiClient(env("UNIFI_URL", ""), env("UNIFI_SITE_ID", ""), env("UNIFI_API_KEY", "")) as client:
            found = client.matching(needle)
        observe_rules(found)
        scope_uptime_rules_once()
        rules = list_rules()
        discovered = [rule for rule in rules if rule["policy_id"] not in known and rule["present"]]
        set_meta("rules_discovered_count", str(len(discovered)))
        set_meta("rules_refreshed_at", now())
        set_meta("rules_error", "")
        return rules


def enforce_grades() -> bool:
    """Move rule-based QoS rules to match the grades. Override rules stay put while an override is active."""
    with _lock:
        return _enforce_grades()


def apply_internet(internet_on: bool, minutes: int) -> None:
    if minutes < 1:
        raise ValueError("duration must be at least a minute")
    with _lock:
        _apply_internet(internet_on, minutes)


def _apply_internet(internet_on: bool, minutes: int) -> None:
    rules = rules_for_override(list_rules())
    if not rules:
        raise UnifiError("no rules are marked for override")
    restore = {rule["policy_id"]: rule["enabled"] for rule in rules}
    _write_rules(rules, internet_on)
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    set_override(internet_on, until.isoformat(), restore)


def clear_internet_override() -> None:
    with _lock:
        current = get_override()
        clear_override()
        try:
            _enforce_grades()
        finally:
            _restore(restore_after_override(current.get("restore") or {}, list_rules()))


def expire_override_if_needed() -> None:
    with _lock:
        current = get_override()
        if not current["active"] or not current["until_at"]:
            return
        until = datetime.fromisoformat(current["until_at"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until > datetime.now(timezone.utc):
            return
        restore = current["restore"] or {}
        clear_override()
        try:
            _enforce_grades()
            _restore(restore_after_override(restore, list_rules()))
        except Exception:
            _restore(restore)
            raise


def loop(stop: threading.Event) -> None:
    next_grades = 0.0
    next_rules = 0.0
    while not stop.is_set():
        try:
            expire_override_if_needed()
        except Exception:
            log.exception("override expiry failed")
        now_s = datetime.now(timezone.utc).timestamp()
        if now_s >= next_grades:
            try:
                grade_sync()
            except Exception as exc:
                log.exception("powerschool sync failed")
                set_meta("sync_error", str(exc))
            next_grades = datetime.now(timezone.utc).timestamp() + 3600
        if env("UNIFI_API_KEY") and now_s >= next_rules:
            try:
                refresh_rules()
            except Exception as exc:
                log.exception("unifi refresh failed")
                set_meta("rules_error", str(exc))
            next_rules = datetime.now(timezone.utc).timestamp() + 300
        if env("UNIFI_API_KEY"):
            try:
                enforce_grades()
            except Exception as exc:
                log.exception("grade rule failed")
                set_meta("rules_error", str(exc))
        stop.wait(30)


def _grade_sync() -> dict:
    student_name = env("PS_STUDENT")
    if not student_name:
        raise PowerSchoolError("PS_STUDENT is not set")
    with PowerSchoolClient(env("PS_LOGIN_URL", ""), env("PS_USER", ""), env("PS_PASSWORD", "")) as client:
        home = client.login()
        students = parse_students(home)
        match = next((student for student in students if student.first_name.casefold() == student_name.casefold()), None)
        if match is None:
            names = ", ".join(student.first_name for student in students)
            raise PowerSchoolError(f"PS_STUDENT={student_name!r} did not match one student ({names})")
        home = client.select_student(match.portal_id)
        if grades_disabled(home):
            raise PowerSchoolError("grades are disabled by the school for this student")
        context = parse_missing_context(client.missing_page())
        missing = client.missing_assignments(context["year_id"], context["student_dcid"])
        courses = parse_courses(home)
        today = datetime.now(zone_for(display_timezone())).date()
        collected = []
        errors = []
        for course in courses:
            term = current_term(course, today)
            if term is None or not term.scores_path:
                continue
            try:
                collected.extend(_course_assignments(client, course.name, term))
            except PowerSchoolError as exc:
                errors.append(f"{course.name}: {exc}")
        merged = _merge_missing(collected, missing)
        quarter_code = current_quarter_code(courses, today) or context.get("current_store_code")
        course_rows = []
        for course in courses:
            term = latest_quarter(course, today, quarter_code)
            course_rows.append(
                {
                    "period": course.period,
                    "name": course.name,
                    "store_code": term.store_code if term else "",
                    "grade": term.grade if term else None,
                }
            )
    replace_assignments([item.to_dict() for item in merged])
    set_meta("courses_json", json.dumps(course_rows))
    set_meta("sync_at", now())
    set_meta("sync_error", "; ".join(errors))
    set_meta("student_name", match.full_name or match.first_name)
    log.info("synced %s assignments for %s", len(merged), student_name)
    _enforce_grades()
    return {"assignments": len(merged), "errors": errors}


def _override_active() -> bool:
    current = get_override()
    if not current["active"] or not current.get("until_at"):
        return False
    until = datetime.fromisoformat(current["until_at"])
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _enforce_grades() -> bool:
    held = _override_active()
    scope_uptime_rules_once()
    cutoff = threshold()
    problems = unexcused_problems(list_assignments(), excused_ids(), cutoff)
    allow = grades_allow_internet(problems)
    rules = rules_for_grades(list_rules(), override_active=held)
    if not rules:
        return False
    if rules_allow_internet(rules) is allow:
        return True
    log.info("grade rule sets internet %s", "on" if allow else "off")
    _write_rules(rules, allow)
    return True


def _course_assignments(client: PowerSchoolClient, course_name: str, term):
    query = parse_qs(urlparse(term.scores_path).query)
    begin = datetime.strptime(query["begdate"][0], "%m/%d/%Y").date().isoformat()
    end = datetime.strptime(query["enddate"][0], "%m/%d/%Y").date().isoformat()
    html = client.scores_page(term.scores_path)
    section_id, student_dcid = parse_scores_meta(html)
    if not section_id or not student_dcid:
        raise PowerSchoolError("scores page did not include a section id")
    payload = client.lookup_assignments(
        section_id,
        student_dcid,
        begin,
        end,
        client.guardian_url(term.scores_path),
    )
    return assignments_from_lookup(payload, course_name, term.store_code)


def _merge_missing(collected, missing):
    by_key = {(item.course, item.name, item.due_date): item for item in collected}
    for item in missing:
        key = (item.course, item.name, item.due_date)
        existing = by_key.get(key)
        if existing is not None:
            existing.missing = True
            continue
        from ps_internet.parse import ScoredAssignment

        by_key[key] = ScoredAssignment(
            assignment_id=f"missing-{item.assignment_id}",
            course=item.course,
            name=item.name,
            due_date=item.due_date,
            store_code=item.store_code,
            percent=None,
            points=None,
            max_points=None,
            missing=True,
            exempt=False,
            letter=None,
        )
    rows = list(by_key.values())
    rows.sort(key=lambda item: (item.due_date or "", item.course, item.name, item.assignment_id))
    return rows


def _write_rules(rules: list[dict], internet_on: bool) -> None:
    prefix = env("UNIFI_RULE_MATCH", "") or ""
    with UnifiClient(env("UNIFI_URL", ""), env("UNIFI_SITE_ID", ""), env("UNIFI_API_KEY", "")) as client:
        for rule in rules:
            enabled = desired_enabled(rule["when_internet"], internet_on)
            client.set_enabled(rule["policy_id"], enabled, name_prefix=prefix)
            set_rule_enabled(rule["policy_id"], enabled)


def _restore(restore: dict) -> None:
    if not restore:
        return
    prefix = env("UNIFI_RULE_MATCH", "") or ""
    with UnifiClient(env("UNIFI_URL", ""), env("UNIFI_SITE_ID", ""), env("UNIFI_API_KEY", "")) as client:
        for policy_id, enabled in restore.items():
            if enabled is None:
                continue
            client.set_enabled(policy_id, bool(enabled), name_prefix=prefix)
            set_rule_enabled(policy_id, bool(enabled))


def scheduler_enabled() -> bool:
    return os.environ.get("PS_SCHEDULER", "1") != "0"
