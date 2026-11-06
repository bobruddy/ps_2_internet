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
    display_timezone,
    excused_ids,
    get_meta,
    get_override,
    list_assignments,
    list_rules,
    set_excuse,
    set_meta,
    set_rule_scope,
    threshold,
)
from ps_internet.jobs import (
    apply_internet,
    clear_internet_override,
    enforce_grades,
    expire_override_if_needed,
    grade_sync,
    loop,
    refresh_rules,
    scheduler_enabled,
)
from ps_internet.status import TIMEZONES, explain, format_local, quarter_grades, rules_allow_internet, unexcused_problems
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
    expire_override_if_needed()
    view = _status_view()
    return templates.TemplateResponse(
        request,
        "status.html",
        {"request": request, "view": view, "sync_at": _local(get_meta("sync_at"))},
    )


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
    expire_override_if_needed()
    cutoff = threshold()
    assignments = list_assignments()
    excused = excused_ids()
    problems = unexcused_problems(assignments, excused, cutoff)
    excused_rows = [row for row in assignments if row["assignment_id"] in excused]
    courses = json.loads(get_meta("courses_json", "[]") or "[]")
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "request": request,
            "csrf": _csrf(request),
            "notice": request.query_params.get("notice", ""),
            "error": request.query_params.get("error", ""),
            "threshold": cutoff,
            "problems": problems,
            "excused_rows": excused_rows,
            "quarters": quarter_grades(courses),
            "rules": list_rules(),
            "rule_match": env("UNIFI_RULE_MATCH", ""),
            "override": get_override(),
            "override_until": _local((get_override() or {}).get("until_at")),
            "sync_at": _local(get_meta("sync_at")),
            "sync_error": get_meta("sync_error", "") or "",
            "rules_error": get_meta("rules_error", "") or "",
            "rules_at": _local(get_meta("rules_refreshed_at")),
            "timezone": display_timezone(),
            "timezones": TIMEZONES,
            "student": get_meta("student_name", "") or "",
        },
    )


@app.post("/admin/excuse")
def excuse(request: Request, assignment_id: str = Form(...), excused: str = Form(...), csrf: str = Form("")):
    _guard(request, csrf)
    set_excuse(assignment_id, excused == "1")
    if _wants_json(request):
        try:
            enforce_grades()
        except Exception as exc:
            log.exception("grade rule failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})
    return _after_grade_change("Saved.")


@app.post("/admin/threshold")
def save_threshold(request: Request, percent: str = Form(...), csrf: str = Form("")):
    _guard(request, csrf)
    try:
        value = float(percent)
    except ValueError:
        return _back_error("Cutoff must be a number.")
    if value <= 0 or value > 100:
        return _back_error("Cutoff must be between 1 and 100.")
    set_meta("threshold_percent", str(value))
    return _after_grade_change("Cutoff saved.")


@app.post("/admin/timezone")
def save_timezone(request: Request, timezone_name: str = Form(""), csrf: str = Form("")):
    _guard(request, csrf)
    allowed = {key for key, _label in TIMEZONES}
    if timezone_name not in allowed:
        return _back_error("Pick a time zone from the list.")
    set_meta("display_timezone", timezone_name)
    return _back("Time zone saved.")


@app.post("/admin/rules")
def save_rule(
    request: Request,
    policy_id: str = Form(...),
    when_internet: str = Form(...),
    csrf: str = Form(""),
    use_rules: str = Form(""),
    use_override: str = Form(""),
):
    _guard(request, csrf)
    try:
        set_rule_scope(policy_id, use_rules == "1", use_override == "1", when_internet)
    except (KeyError, ValueError):
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "That rule could not be updated."}, status_code=400)
        return _back_error("That rule could not be updated.")
    if _wants_json(request):
        return JSONResponse({"ok": True})
    return _back("Rule saved.")


@app.post("/admin/rules/refresh")
def rules_refresh(request: Request, csrf: str = Form("")):
    _guard(request, csrf)
    try:
        refresh_rules()
    except UnifiError as exc:
        return _back_error(str(exc))
    except Exception as exc:
        log.exception("rule refresh failed")
        return _back_error(str(exc))
    found = len(list_rules())
    discovered = int(get_meta("rules_discovered_count", "0") or "0")
    if discovered:
        notice = f"Discovered {discovered} new rule{'s' if discovered != 1 else ''}. {found} on the gateway match."
    else:
        notice = f"No new rules. {found} already known."
    return _back(notice)


@app.post("/admin/sync")
def sync_now(request: Request, csrf: str = Form("")):
    _guard(request, csrf)
    try:
        grade_sync()
    except Exception as exc:
        log.exception("manual sync failed")
        return _back_error(str(exc))
    return _back("Grades refreshed.")


@app.post("/admin/override")
def override(
    request: Request,
    internet: str = Form(...),
    minutes: int = Form(...),
    csrf: str = Form(""),
):
    _guard(request, csrf)
    if internet not in {"on", "off"}:
        return _back_error("Choose on or off.")
    try:
        apply_internet(internet == "on", minutes)
    except (UnifiError, ValueError) as exc:
        return _back_error(str(exc))
    except Exception as exc:
        log.exception("override failed")
        return _back_error(str(exc))
    return _back("Override applied.")


@app.post("/admin/override/clear")
def override_clear(request: Request, csrf: str = Form("")):
    _guard(request, csrf)
    try:
        clear_internet_override()
    except Exception as exc:
        log.exception("clear override failed")
        return _back_error(str(exc))
    return _back("Override cleared and the previous rule states restored.")


def _status_view() -> dict:
    cutoff = threshold()
    problems = unexcused_problems(list_assignments(), excused_ids(), cutoff)
    rules = list_rules()
    internet_on = rules_allow_internet(rules)
    view = explain(
        configured=internet_on is not None,
        internet_on=internet_on,
        override=get_override(),
        problems=problems,
        now=datetime.now(timezone.utc),
        zone=display_timezone(),
    )
    view["quarters"] = quarter_grades(json.loads(get_meta("courses_json", "[]") or "[]"))
    view["threshold"] = cutoff
    return view


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


def _after_grade_change(notice: str) -> RedirectResponse:
    try:
        applied = enforce_grades()
    except UnifiError as exc:
        return _back_error(str(exc))
    except Exception as exc:
        log.exception("grade rule failed")
        return _back_error(str(exc))
    if not applied:
        return _back(notice + " No QoS rules are in scope, so internet was not changed.")
    return _back(notice)


def _back(notice: str) -> RedirectResponse:
    return RedirectResponse(f"/admin?notice={quote(notice)}", status_code=303)


def _back_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/admin?error={quote(message)}", status_code=303)


def _local(value: str | None) -> str:
    return format_local(value, display_timezone())
