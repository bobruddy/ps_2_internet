"""PowerSchool sync, QoS pause and resume, and timed internet overrides."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from ps_internet.client import PowerSchoolClient, PowerSchoolError
from ps_internet.config import env
from ps_internet.db import (
    LEGACY_STUDENT_ID,
    clear_expired_excuses,
    clear_override,
    display_timezone,
    ensure_student,
    excused_ids,
    get_meta,
    get_override,
    get_student,
    list_assignments,
    list_rules,
    list_students,
    mark_students_present,
    now,
    observe_rules,
    rekey_student,
    replace_assignments,
    set_meta,
    set_override,
    set_rule_enabled,
    threshold,
    update_student,
)
from ps_internet.parse import (
    assignment_window,
    assignments_from_lookup,
    current_quarter_code,
    latest_quarter,
    quarter_code_for_date,
    scores_terms,
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
    local_today,
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


def refresh_rules(student_id: str) -> list[dict]:
    with _lock:
        return _refresh_rules(student_id)


def enforce_grades(student_id: str | None = None) -> bool:
    """Move one student's rule-based QoS rules to match that student's grades."""
    with _lock:
        if student_id is None:
            applied = False
            for student in list_students():
                applied = _enforce_grades(student["student_id"]) or applied
            return applied
        return _enforce_grades(student_id)


def apply_internet(student_id: str, internet_on: bool, minutes: int) -> None:
    if minutes < 1:
        raise ValueError("duration must be at least a minute")
    with _lock:
        _apply_internet(student_id, internet_on, minutes)


def _apply_internet(student_id: str, internet_on: bool, minutes: int) -> None:
    rules = rules_for_override(list_rules(student_id))
    if not rules:
        raise UnifiError("no rules are marked for override")
    restore = {rule["policy_id"]: rule["enabled"] for rule in rules}
    _write_rules(student_id, rules, internet_on)
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    set_override(student_id, internet_on, until.isoformat(), restore)


def clear_internet_override(student_id: str) -> None:
    with _lock:
        current = get_override(student_id)
        clear_override(student_id)
        try:
            _enforce_grades(student_id)
        finally:
            _restore(student_id, restore_after_override(current.get("restore") or {}, list_rules(student_id)))


def expire_temporary_excuses() -> None:
    """A day excuse ends on its own, then grades decide internet again."""
    with _lock:
        for student_id in clear_expired_excuses():
            try:
                _enforce_grades(student_id)
            except Exception:
                log.exception("grade rule failed after an excuse ended")


def expire_override_if_needed() -> None:
    with _lock:
        for student in list_students():
            _expire_one(student["student_id"])


def _expire_one(student_id: str) -> None:
    current = get_override(student_id)
    if not current["active"] or not current["until_at"]:
        return
    until = datetime.fromisoformat(current["until_at"])
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if until > datetime.now(timezone.utc):
        return
    restore = current["restore"] or {}
    clear_override(student_id)
    try:
        _enforce_grades(student_id)
        _restore(student_id, restore_after_override(restore, list_rules(student_id)))
    except Exception:
        _restore(student_id, restore)
        raise


def loop(stop: threading.Event) -> None:
    next_grades = 0.0
    next_rules = 0.0
    while not stop.is_set():
        try:
            expire_temporary_excuses()
        except Exception:
            log.exception("excuse expiry failed")
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
            for student in list_students(present_only=True):
                try:
                    refresh_rules(student["student_id"])
                except Exception as exc:
                    log.exception("unifi refresh failed")
                    update_student(student["student_id"], rules_error=str(exc))
            next_rules = datetime.now(timezone.utc).timestamp() + 300
        if env("UNIFI_API_KEY"):
            for student in list_students(present_only=True):
                try:
                    enforce_grades(student["student_id"])
                except Exception as exc:
                    log.exception("grade rule failed")
                    update_student(student["student_id"], rules_error=str(exc))
        stop.wait(30)


def _grade_sync() -> dict:
    with PowerSchoolClient(env("PS_LOGIN_URL", ""), env("PS_USER", ""), env("PS_PASSWORD", "")) as client:
        home = client.login()
        found = parse_students(home)
        if not found:
            raise PowerSchoolError("the portal account did not list any students")
        seen: set[str] = set()
        total = 0
        errors: list[str] = []
        for portal_student in found:
            student_id = _remember_student(portal_student.portal_id, portal_student.first_name, portal_student.full_name)
            seen.add(student_id)
            try:
                count, course_errors = _sync_student(client, student_id)
            except PowerSchoolError as exc:
                update_student(student_id, sync_error=str(exc))
                errors.append(f"{portal_student.first_name}: {exc}")
                continue
            total += count
            errors.extend(course_errors)
            try:
                _enforce_grades(student_id)
            except Exception as exc:
                log.exception("grade rule failed")
                update_student(student_id, rules_error=str(exc))
        mark_students_present(seen)
    return {"assignments": total, "errors": errors, "students": sorted(seen)}


def _remember_student(portal_id: str, first_name: str, full_name: str | None) -> str:
    """Attach a portal student to a row. An older one-student database keeps its settings."""
    legacy = get_student(LEGACY_STUDENT_ID)
    if legacy is not None and legacy["first_name"].casefold() == first_name.casefold():
        rekey_student(LEGACY_STUDENT_ID, portal_id)
    fresh = get_student(portal_id) is None
    ensure_student(portal_id, first_name, full_name)
    wanted = (env("PS_STUDENT", "") or "").strip()
    if fresh and wanted and wanted.casefold() == first_name.casefold() and get_meta("prefix_applied") != "1":
        prefix = (env("UNIFI_RULE_MATCH", "") or "").strip()
        if prefix:
            update_student(portal_id, rule_prefix=prefix)
            set_meta("prefix_applied", "1")
    return portal_id


def _sync_student(client: PowerSchoolClient, student_id: str) -> tuple[int, list[str]]:
    home = client.select_student(student_id)
    selected = next((student for student in parse_students(home) if student.selected), None)
    if selected is not None:
        update_student(student_id, full_name=selected.full_name or "", first_name=selected.first_name)
    if grades_disabled(home):
        update_student(student_id, sync_error="grades are disabled by the school for this student", sync_at=now())
        return 0, []
    context = parse_missing_context(client.missing_page())
    missing = client.missing_assignments(context["year_id"], context["student_dcid"])
    courses = parse_courses(home)
    today = datetime.now(zone_for(display_timezone())).date()
    collected = []
    errors = []
    for course in courses:
        window = assignment_window(course)
        if window is None or not scores_terms(course):
            continue
        try:
            collected.extend(_collect_course(client, course, window))
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
    replace_assignments(student_id, [item.to_dict() for item in merged])
    update_student(
        student_id,
        courses_json=json.dumps(course_rows),
        sync_at=now(),
        sync_error="; ".join(errors),
    )
    log.info("synced %s assignments for %s", len(merged), student_id)
    return len(merged), errors


def _refresh_rules(student_id: str) -> list[dict]:
    student = get_student(student_id)
    if student is None:
        return []
    known = {rule["policy_id"] for rule in list_rules(student_id)}
    needle = (student["rule_prefix"] or "").strip()
    if not needle:
        update_student(student_id, rules_error="Set a QoS rule prefix for this student.")
        return list_rules(student_id)
    with UnifiClient(env("UNIFI_URL", ""), env("UNIFI_SITE_ID", ""), env("UNIFI_API_KEY", "")) as client:
        found = client.matching(needle)
    observe_rules(student_id, found)
    rules = list_rules(student_id)
    discovered = [rule for rule in rules if rule["policy_id"] not in known and rule["present"]]
    set_meta("rules_discovered_count", str(len(discovered)))
    update_student(student_id, rules_refreshed_at=now(), rules_error="")
    return rules


def _override_active(student_id: str) -> bool:
    current = get_override(student_id)
    if not current["active"] or not current.get("until_at"):
        return False
    until = datetime.fromisoformat(current["until_at"])
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _enforce_grades(student_id: str) -> bool:
    student = get_student(student_id)
    if student is None:
        return False
    held = _override_active(student_id)
    cutoff = threshold(student_id)
    problems = unexcused_problems(
        list_assignments(student_id),
        excused_ids(student_id),
        cutoff,
        grace_days=int(student.get("grace_days") or 0),
        today=local_today(display_timezone()),
    )
    allow = grades_allow_internet(problems)
    rules = rules_for_grades(list_rules(student_id), override_active=held)
    if not rules or not (student["rule_prefix"] or "").strip():
        return False
    if rules_allow_internet(rules) is allow:
        return True
    log.info("grade rule sets internet %s for %s", "on" if allow else "off", student_id)
    _write_rules(student_id, rules, allow)
    return True


def _collect_course(client: PowerSchoolClient, course, window):
    """Ask for the whole year. If the year page has no section id, try a quarter page."""
    failure = None
    for term in scores_terms(course):
        try:
            rows = _course_assignments(client, course.name, term, window)
        except PowerSchoolError as exc:
            failure = exc
            continue
        _stamp_quarters(course, rows)
        return rows
    if failure is not None:
        raise failure
    return []


def _stamp_quarters(course, rows) -> None:
    for row in rows:
        due = None
        if row.due_date:
            try:
                due = date.fromisoformat(str(row.due_date)[:10])
            except ValueError:
                due = None
        code = quarter_code_for_date(course, due)
        if code:
            row.store_code = code


def _course_assignments(client: PowerSchoolClient, course_name: str, term, window):
    begin, end = window
    html = client.scores_page(term.scores_path)
    section_id, student_dcid = parse_scores_meta(html)
    if not section_id or not student_dcid:
        raise PowerSchoolError("scores page did not include a section id")
    payload = client.lookup_assignments(
        section_id,
        student_dcid,
        begin.isoformat(),
        end.isoformat(),
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


def _write_rules(student_id: str, rules: list[dict], internet_on: bool) -> None:
    student = get_student(student_id) or {}
    prefix = (student.get("rule_prefix") or "").strip()
    with UnifiClient(env("UNIFI_URL", ""), env("UNIFI_SITE_ID", ""), env("UNIFI_API_KEY", "")) as client:
        for rule in rules:
            enabled = desired_enabled(rule["when_internet"], internet_on)
            client.set_enabled(rule["policy_id"], enabled, name_prefix=prefix)
            set_rule_enabled(student_id, rule["policy_id"], enabled)


def _restore(student_id: str, restore: dict) -> None:
    if not restore:
        return
    student = get_student(student_id) or {}
    prefix = (student.get("rule_prefix") or "").strip()
    with UnifiClient(env("UNIFI_URL", ""), env("UNIFI_SITE_ID", ""), env("UNIFI_API_KEY", "")) as client:
        for policy_id, enabled in restore.items():
            if enabled is None:
                continue
            client.set_enabled(policy_id, bool(enabled), name_prefix=prefix)
            set_rule_enabled(student_id, policy_id, bool(enabled))


def scheduler_enabled() -> bool:
    return os.environ.get("PS_SCHEDULER", "1") != "0"
