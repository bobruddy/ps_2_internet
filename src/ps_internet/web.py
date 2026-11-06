"""Status page for the household, and an admin page for excuses, rules, and overrides."""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ps_internet.config import env, load_dotenv
from ps_internet.db import (
    active_excuses,
    display_timezone,
    excused_ids,
    get_meta,
    get_override,
    get_student,
    list_assignments,
    list_rules,
    list_students,
    set_excuse,
    set_meta,
    set_rule_scope,
    student_by_token,
    threshold,
    update_student,
)
from ps_internet.jobs import (
    apply_internet,
    clear_internet_override,
    enforce_grades,
    expire_override_if_needed,
    expire_temporary_excuses,
    grade_sync,
    loop,
    refresh_rules,
    scheduler_enabled,
)
from ps_internet.status import (
    TIMEZONES,
    assignment_rows,
    explain,
    format_local,
    format_local_day,
    local_today,
    public_problem_rows,
    quarter_grades,
    rules_allow_internet,
    unexcused_problems,
)
from ps_internet.unifi import UnifiError

log = logging.getLogger("ps_internet")
ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))

load_dotenv()
app = FastAPI(title="Internet")
app.add_middleware(SessionMiddleware, secret_key=env("ADMIN_SESSION_SECRET", "") or secrets.token_hex(32), same_site="lax")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.on_event("startup")
def _start_scheduler() -> None:
    if not scheduler_enabled():
        return
    stop = threading.Event()
    app.state.stop = stop
    thread = threading.Thread(target=loop, args=(stop,), name="ps-scheduler", daemon=True)
    thread.start()
    app.state.scheduler = thread


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    stop = getattr(app.state, "stop", None)
    if stop is not None:
        stop.set()


@app.get("/", response_class=HTMLResponse)
def status_page(request: Request):
    return _private_page(request)


@app.get("/s/{token}", response_class=HTMLResponse)
def shared_status(request: Request, token: str):
    student = student_by_token(token)
    if student is None or not student["public_enabled"]:
        return _private_page(request)
    expire_override_if_needed()
    expire_temporary_excuses()
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "request": request,
            "view": _status_view(student["student_id"]),
            "sync_at": _local(student.get("sync_at")),
            "student_name": student["first_name"],
        },
    )


def _private_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "private.html", {"request": request})


@app.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "csrf": _csrf(request),
            "error": request.query_params.get("error", ""),
            "configured": bool(env("ADMIN_PASSWORD")),
        },
    )


@app.post("/admin/login")
def login(request: Request, password: str = Form(""), csrf: str = Form("")):
    _check_csrf(request, csrf)
    expected = env("ADMIN_PASSWORD") or ""
    if not expected or not secrets.compare_digest(password, expected):
        return RedirectResponse("/admin/login?error=1", status_code=303)
    request.session["admin"] = True
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/logout")
def logout(request: Request, csrf: str = Form("")):
    _check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/admin/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "students.html",
        {**_admin_common(request), "students": list_students(present_only=True)},
    )


@app.get("/admin/students/{student_id}", response_class=HTMLResponse)
def admin_student(request: Request, student_id: str):
    student = _open_student(request, student_id)
    expire_override_if_needed()
    expire_temporary_excuses()
    cutoff = threshold(student_id)
    assignments = list_assignments(student_id)
    excuses = active_excuses(student_id)
    courses = json.loads(student["courses_json"] or "[]")
    today = local_today(display_timezone())
    grace = int(student.get("grace_days") or 0)
    rows = assignment_rows(assignments, set(excuses), cutoff, grace_days=grace, today=today)
    for row in rows:
        until = excuses.get(row["assignment_id"])
        row["excuse_until"] = _local(until) if until else ""
        row["excuse_until_short"] = format_local_day(until) if until else ""
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            **_admin_common(request),
            "student": student,
            "base": f"/admin/students/{student_id}",
            "public_url": _public_url(request, student),
            "threshold": cutoff,
            "assignments": rows,
            "quarters": quarter_grades(courses, {item["course"] for item in assignments}),
            "override": get_override(student_id),
            "override_until": _local((get_override(student_id) or {}).get("until_at")),
            "sync_at": _local(student.get("sync_at")),
            "sync_error": student.get("sync_error") or "",
        },
    )


@app.get("/admin/students/{student_id}/settings", response_class=HTMLResponse)
def admin_settings(request: Request, student_id: str):
    student = _open_student(request, student_id)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            **_admin_common(request),
            "student": student,
            "base": f"/admin/students/{student_id}",
            "threshold": threshold(student_id),
            "rules": list_rules(student_id),
            "rule_match": student["rule_prefix"],
            "sync_at": _local(student.get("sync_at")),
            "sync_error": student.get("sync_error") or "",
            "rules_error": student.get("rules_error") or "",
            "rules_at": _local(student.get("rules_refreshed_at")),
            "timezone": display_timezone(),
            "timezones": TIMEZONES,
        },
    )


@app.post("/admin/students/{student_id}/excuse")
def excuse(
    request: Request,
    student_id: str,
    assignment_id: str = Form(...),
    choice: str = Form(""),
    excused: str = Form(""),
    days: str = Form(""),
    csrf: str = Form(""),
):
    _guard(request, csrf)
    _open_student(request, student_id)
    try:
        held, until = _save_excuse(student_id, assignment_id, choice, excused, days)
    except ValueError as exc:
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return _back_error(student_id, str(exc))
    if _wants_json(request):
        try:
            enforce_grades(student_id)
        except Exception as exc:
            log.exception("grade rule failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        shown = _local(until) if until else ""
        return JSONResponse(
            {"ok": True, "excused": held, "until": shown, "until_short": format_local_day(until) if until else ""}
        )
    return _after_grade_change(student_id, "Excuse saved." if held else "Excuse cleared.")


def _save_excuse(
    student_id: str,
    assignment_id: str,
    choice: str,
    excused: str,
    days: str,
) -> tuple[bool, str | None]:
    picked = choice.strip()
    if not picked:
        picked = days.strip()
        if excused != "1":
            picked = "clear"
        elif not picked:
            picked = "forever"
    if picked == "clear":
        set_excuse(student_id, assignment_id, False)
        return False, None
    if picked == "forever":
        set_excuse(student_id, assignment_id, True)
        return True, None
    if not picked.isdigit():
        raise ValueError("Pick 1 to 5 days, or Excuse for no end.")
    until = set_excuse(student_id, assignment_id, True, days=int(picked))
    return True, until


@app.post("/admin/students/{student_id}/threshold")
def save_threshold(request: Request, student_id: str, percent: str = Form(...), csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    try:
        value = float(percent)
    except ValueError:
        return _back_error(student_id, "Cutoff must be a number.", settings=True)
    if value <= 0 or value > 100:
        return _back_error(student_id, "Cutoff must be between 1 and 100.", settings=True)
    update_student(student_id, threshold_percent=str(value))
    if _wants_json(request):
        try:
            enforce_grades(student_id)
        except Exception as exc:
            log.exception("grade rule failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})
    return _after_grade_change(student_id, "Cutoff saved.", settings=True)


@app.post("/admin/students/{student_id}/grace")
def save_grace(request: Request, student_id: str, days: str = Form("0"), csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    try:
        value = int(days)
    except ValueError:
        return _back_error(student_id, "Grace period must be a number of business days.", settings=True)
    if value < 0 or value > 30:
        return _back_error(student_id, "Grace period must be from 0 to 30 business days.", settings=True)
    update_student(student_id, grace_days=value)
    if _wants_json(request):
        try:
            enforce_grades(student_id)
        except Exception as exc:
            log.exception("grade rule failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})
    return _after_grade_change(student_id, "Grace period saved.", settings=True)


@app.post("/admin/students/{student_id}/prefix")
def save_prefix(request: Request, student_id: str, rule_prefix: str = Form(""), csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    update_student(student_id, rule_prefix=rule_prefix.strip())
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return _back(student_id, "QoS prefix saved.", settings=True)


@app.post("/admin/students/{student_id}/public")
def save_public(request: Request, student_id: str, public_enabled: str = Form(""), csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    update_student(student_id, public_enabled=public_enabled == "1")
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return _back(student_id, "Shared page updated.")


@app.post("/admin/students/{student_id}/timezone")
def save_timezone(request: Request, student_id: str, timezone_name: str = Form(""), csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    allowed = {key for key, _label in TIMEZONES}
    if timezone_name not in allowed:
        return _back_error(student_id, "Pick a time zone from the list.", settings=True)
    set_meta("display_timezone", timezone_name)
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return _back(student_id, "Time zone saved.", settings=True)


@app.post("/admin/students/{student_id}/rules")
def save_rule(
    request: Request,
    student_id: str,
    policy_id: str = Form(...),
    when_internet: str = Form(...),
    csrf: str = Form(""),
    use_rules: str = Form(""),
    use_override: str = Form(""),
):
    _guard(request, csrf)
    _open_student(request, student_id)
    try:
        set_rule_scope(student_id, policy_id, use_rules == "1", use_override == "1", when_internet)
    except (KeyError, ValueError):
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "That rule could not be updated."}, status_code=400)
        return _back_error(student_id, "That rule could not be updated.", settings=True)
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return _back(student_id, "Rule saved.", settings=True)


@app.post("/admin/students/{student_id}/rules/refresh")
def rules_refresh(request: Request, student_id: str, csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    try:
        refresh_rules(student_id)
    except UnifiError as exc:
        return _back_error(student_id, str(exc), settings=True)
    except Exception as exc:
        log.exception("rule refresh failed")
        return _back_error(student_id, str(exc), settings=True)
    found = len(list_rules(student_id))
    discovered = int(get_meta("rules_discovered_count", "0") or "0")
    if discovered:
        notice = f"Discovered {discovered} new rule{'s' if discovered != 1 else ''}. {found} on the gateway match."
    else:
        notice = f"No new rules. {found} already known."
    return _back(student_id, notice, settings=True)


@app.post("/admin/sync")
@app.post("/admin/students/{student_id}/sync")
def sync_now(request: Request, student_id: str | None = None, csrf: str = Form("")):
    _guard(request, csrf)
    if student_id is not None:
        _open_student(request, student_id)
    try:
        grade_sync()
    except Exception as exc:
        log.exception("manual sync failed")
        if student_id is None:
            return RedirectResponse(f"/admin?error={quote(str(exc))}", status_code=303)
        return _back_error(student_id, str(exc))
    if student_id is None:
        return RedirectResponse(f"/admin?notice={quote('Students refreshed.')}", status_code=303)
    return _back(student_id, "Grades refreshed.")


@app.post("/admin/students/{student_id}/override")
def override(
    request: Request,
    student_id: str,
    internet: str = Form(...),
    minutes: int = Form(...),
    csrf: str = Form(""),
):
    _guard(request, csrf)
    _open_student(request, student_id)
    if internet not in {"on", "off"}:
        return _back_error(student_id, "Choose on or off.")
    try:
        apply_internet(student_id, internet == "on", minutes)
    except (UnifiError, ValueError) as exc:
        return _back_error(student_id, str(exc))
    except Exception as exc:
        log.exception("override failed")
        return _back_error(student_id, str(exc))
    return _back(student_id, "Override applied.")


@app.post("/admin/students/{student_id}/override/clear")
def override_clear(request: Request, student_id: str, csrf: str = Form("")):
    _guard(request, csrf)
    _open_student(request, student_id)
    try:
        clear_internet_override(student_id)
    except Exception as exc:
        log.exception("clear override failed")
        return _back_error(student_id, str(exc))
    return _back(student_id, "Override cleared and the previous rule states restored.")


def _admin_common(request: Request) -> dict:
    return {
        "request": request,
        "csrf": _csrf(request),
        "notice": request.query_params.get("notice", ""),
        "error": request.query_params.get("error", ""),
    }


def _status_view(student_id: str) -> dict:
    student = get_student(student_id) or {}
    cutoff = threshold(student_id)
    assignments = list_assignments(student_id)
    grace = int(student.get("grace_days") or 0)
    problems = unexcused_problems(
        assignments,
        excused_ids(student_id),
        cutoff,
        grace_days=grace,
        today=local_today(display_timezone()),
    )
    rules = list_rules(student_id)
    internet_on = rules_allow_internet(rules)
    view = explain(
        configured=internet_on is not None,
        internet_on=internet_on,
        override=get_override(student_id),
        problems=problems,
        now=datetime.now(timezone.utc),
        zone=display_timezone(),
    )
    view["quarters"] = quarter_grades(
        json.loads(student.get("courses_json") or "[]"),
        {item["course"] for item in assignments},
    )
    view["threshold"] = cutoff
    view["grace_days"] = grace
    listed = public_problem_rows(
        assignments,
        active_excuses(student_id),
        cutoff,
        grace_days=grace,
        today=local_today(display_timezone()),
    )
    for item in listed:
        item["excuse_until"] = format_local_day(item["excuse_until"]) if item["excuse_until"] else ""
    view["listed"] = listed
    return view


def _open_student(request: Request, student_id: str) -> dict:
    if not request.session.get("admin"):
        raise _Redirect("/admin/login")
    student = get_student(student_id)
    if student is None:
        raise _Redirect("/admin?error=That+student+is+not+in+the+list")
    return student


def _public_url(request: Request, student: dict) -> str:
    root = str(request.base_url).rstrip("/")
    return f"{root}/s/{student['public_token']}"


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


class _Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def _check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf") or ""
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise _Redirect("/admin/login?error=1")


def _guard(request: Request, token: str) -> None:
    if not request.session.get("admin"):
        raise _Redirect("/admin/login")
    _check_csrf(request, token)


@app.exception_handler(_Redirect)
def _redirected(request: Request, exc: _Redirect):
    return RedirectResponse(exc.url, status_code=303)


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _after_grade_change(student_id: str, notice: str, settings: bool = False) -> RedirectResponse:
    try:
        applied = enforce_grades(student_id)
    except UnifiError as exc:
        return _back_error(student_id, str(exc), settings=settings)
    except Exception as exc:
        log.exception("grade rule failed")
        return _back_error(student_id, str(exc), settings=settings)
    if not applied:
        return _back(student_id, notice + " No QoS rules are in scope, so internet was not changed.", settings=settings)
    return _back(student_id, notice, settings=settings)


def _student_path(student_id: str, settings: bool = False) -> str:
    path = f"/admin/students/{student_id}"
    if settings:
        path += "/settings"
    return path


def _back(student_id: str, notice: str, settings: bool = False) -> RedirectResponse:
    return RedirectResponse(f"{_student_path(student_id, settings)}?notice={quote(notice)}", status_code=303)


def _back_error(student_id: str, message: str, settings: bool = False) -> RedirectResponse:
    return RedirectResponse(f"{_student_path(student_id, settings)}?error={quote(message)}", status_code=303)


def _local(value: str | None) -> str:
    return format_local(value, display_timezone())
