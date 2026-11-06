"""Command line entry points. Credentials come from the environment or a local .env file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ps_internet.client import PowerSchoolClient, PowerSchoolError
from ps_internet.config import load_dotenv
from ps_internet.parse import parse_students


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="ps-internet")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("students", help="List students on the parent account")

    sync = sub.add_parser("sync", help="Save grades and missing assignments as JSON")
    sync.add_argument("--student", default=os.environ.get("PS_STUDENT"), help="First name in the portal switcher")
    sync.add_argument("--out", default="data/latest.json", help="Where to write the snapshot")

    args = parser.parse_args(argv)
    try:
        with _client() as client:
            home = client.login()
            students = parse_students(home)
            if args.command == "students":
                _print_students(students)
                return
            match = _match_student(students, args.student)
            snapshot = client.snapshot(match.portal_id)
    except PowerSchoolError as exc:
        raise SystemExit(str(exc)) from None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2) + "\n")
    _print_snapshot(snapshot, out)


def _client() -> PowerSchoolClient:
    missing = [name for name in ("PS_USER", "PS_PASSWORD", "PS_LOGIN_URL") if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"missing environment values: {', '.join(missing)}")
    return PowerSchoolClient(
        login_url=os.environ["PS_LOGIN_URL"],
        username=os.environ["PS_USER"],
        password=os.environ["PS_PASSWORD"],
    )


def _match_student(students, wanted: str | None):
    if not students:
        raise SystemExit("the portal account did not list any students")
    if not wanted:
        _print_students(students)
        raise SystemExit("set PS_STUDENT to the first name to sync")
    needle = wanted.casefold()
    matches = [student for student in students if student.first_name.casefold() == needle]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(student.first_name for student in students)
    raise SystemExit(f"PS_STUDENT={wanted!r} did not match one student ({names})")


def _print_students(students) -> None:
    for student in students:
        mark = "*" if student.selected else " "
        full = f"  {student.full_name}" if student.full_name else ""
        print(f"{mark} {student.first_name:<12} id={student.portal_id}{full}")


def _print_snapshot(snapshot: dict, out: Path) -> None:
    student = snapshot["student"]
    print(f"{student.get('full_name') or student.get('first_name')} — {student.get('school')}")
    if student.get("grades_disabled"):
        print("Grades and attendance are disabled by the school for this student.")
    else:
        print("Courses")
        for course in snapshot["courses"]:
            grades = []
            for term in course["terms"]:
                if term["grade"]:
                    grades.append(f"{term['store_code']} {term['grade']}")
            shown = ", ".join(grades) if grades else "no published grades"
            print(f"  {course['period']:<10} {course['name']}: {shown}")
    missing = snapshot["missing_assignments"]
    print(f"Missing assignments: {len(missing)}")
    for item in missing:
        print(f"  {item['due_date']}  {item['store_code']:<3} {item['course']}: {item['name']}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
