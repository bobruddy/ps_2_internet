"""HTTP client for a PowerSchool parent account the caller already owns."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ps_internet.parse import (
    grades_disabled,
    missing_from_query,
    parse_courses,
    parse_missing_context,
    parse_school,
    parse_scores_meta,
    parse_students,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MISSING_QUERY = (
    "/ws/schema/query/com.schooldatapowersolutions.student.missingassignments.flat"
)


class PowerSchoolError(Exception):
    pass


class LoginError(PowerSchoolError):
    pass


class PowerSchoolClient:
    def __init__(self, login_url: str, username: str, password: str, client: httpx.Client | None = None):
        parsed = urlparse(login_url)
        if not parsed.scheme or not parsed.netloc:
            raise PowerSchoolError("PS_LOGIN_URL must be an absolute URL")
        self.login_url = login_url
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.username = username
        self.password = password
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=40,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PowerSchoolClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def login(self) -> str:
        self._client.get(self.login_url)
        response = self._client.post(
            f"{self.base_url}/guardian/home.html",
            data={
                "dbpw": self.password,
                "translator_username": "",
                "translator_password": "",
                "translator_ldappassword": "",
                "returnUrl": "",
                "serviceName": "PS Parent Portal",
                "serviceTicket": "",
                "pcasServerUrl": "/",
                "credentialType": "User Id and Password Credential",
                "request_locale": "en_US",
                "account": self.username,
                "pw": self.password,
                "translatorpw": "",
            },
            headers={
                "Origin": self.base_url,
                "Referer": self.login_url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if response.status_code != 200 or 'id="btnLogout"' not in response.text:
            raise LoginError("PowerSchool did not accept the username and password")
        return response.text

    def select_student(self, portal_id: str) -> str:
        response = self._client.post(
            f"{self.base_url}/guardian/home.html",
            data={"selected_student_id": portal_id},
            headers={
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/guardian/home.html",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        return response.text

    def missing_page(self) -> str:
        response = self._client.get(
            f"{self.base_url}/guardian/missingasmts.html",
            headers={"Referer": f"{self.base_url}/guardian/home.html"},
        )
        response.raise_for_status()
        return response.text

    def missing_assignments(self, year_id: str, student_dcid: str) -> list:
        response = self._client.post(
            f"{self.base_url}{MISSING_QUERY}",
            params={"pagesize": "0"},
            json={"curyearid": int(year_id), "studentrn": int(student_dcid)},
            headers={
                "Accept": "application/json",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/guardian/missingasmts.html",
            },
        )
        if response.status_code != 200:
            raise PowerSchoolError(f"missing assignments query returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PowerSchoolError("missing assignments query did not return JSON") from exc
        return missing_from_query(payload)

    def guardian_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}/guardian/{path.lstrip('/')}"

    def scores_page(self, scores_path: str) -> str:
        url = self.guardian_url(scores_path)
        response = self._client.get(url, headers={"Referer": f"{self.base_url}/guardian/home.html"})
        response.raise_for_status()
        return response.text

    def lookup_assignments(self, section_id: str, student_dcid: str, start: str, end: str, referer: str):
        response = self._client.post(
            f"{self.base_url}/ws/xte/assignment/lookup",
            json={
                "section_ids": [int(section_id)],
                "student_ids": [int(student_dcid)],
                "start_date": start,
                "end_date": end,
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": self.base_url,
                "Referer": referer,
                "Content-Type": "application/json;charset=UTF-8",
            },
        )
        if response.status_code != 200:
            raise PowerSchoolError(f"assignment lookup returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise PowerSchoolError("assignment lookup did not return JSON") from exc

    def scores_meta(self, scores_path: str) -> tuple[str | None, str | None]:
        return parse_scores_meta(self.scores_page(scores_path))

    def snapshot(self, portal_id: str) -> dict:
        home = self.select_student(portal_id)
        students = parse_students(home)
        selected = next((student for student in students if student.selected), None)
        context_html = self.missing_page()
        context = parse_missing_context(context_html)
        missing = self.missing_assignments(context["year_id"], context["student_dcid"])
        courses = [] if grades_disabled(home) else parse_courses(home)
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "student": {
                "portal_id": portal_id,
                "first_name": selected.first_name if selected else None,
                "full_name": selected.full_name if selected else None,
                "school": parse_school(home),
                "dcid": context["student_dcid"],
                "year_id": context["year_id"],
                "school_id": context["school_id"],
                "grades_disabled": grades_disabled(home),
            },
            "courses": [course.to_dict() for course in courses],
            "missing_assignments": [item.to_dict() for item in missing],
        }
