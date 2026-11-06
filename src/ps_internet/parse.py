"""Parse PowerSchool parent-portal pages."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, NavigableString

# Year and semester store codes repeat the same assignment as the quarter row.
ROLLUP_STORE_CODES = {"Y1", "Y2", "S1", "S2"}

_STUDENT_HREF = re.compile(r"switchStudent\((\d+)\)")
_STUDENT_NAME = re.compile(r"var studentName = '([^']*)'")


@dataclass
class PortalStudent:
    portal_id: str
    first_name: str
    selected: bool
    full_name: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TermGrade:
    store_code: str
    grade: str | None
    scores_path: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Course:
    period: str
    name: str
    teacher: str | None
    room: str | None
    terms: list[TermGrade]
    absences: str | None
    tardies: str | None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["terms"] = [term.to_dict() for term in self.terms]
        return data


@dataclass
class MissingAssignment:
    assignment_id: str
    course: str
    name: str
    category: str | None
    due_date: str | None
    store_code: str
    teacher: str | None
    score_type: str | None
    missing: bool
    absent: bool
    incomplete: bool

    def to_dict(self) -> dict:
        return asdict(self)


def parse_students(html: str) -> list[PortalStudent]:
    soup = BeautifulSoup(html, "html.parser")
    full_name = None
    match = _STUDENT_NAME.search(html)
    if match:
        full_name = match.group(1)
    students: list[PortalStudent] = []
    student_list = soup.find("ul", id="students-list")
    if student_list is None:
        return students
    for item in student_list.find_all("li", recursive=False):
        link = item.find("a")
        if link is None or not link.get("href"):
            continue
        found = _STUDENT_HREF.search(link["href"])
        if not found:
            continue
        selected = "selected" in (item.get("class") or [])
        students.append(
            PortalStudent(
                portal_id=found.group(1),
                first_name=link.get_text(strip=True),
                selected=selected,
                full_name=full_name if selected else None,
            )
        )
    return students


def parse_school(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    block = soup.find(id="print-school")
    if block is None:
        return None
    school = block.find("span")
    text = (school or block).get_text(" ", strip=True)
    return text or None


def grades_disabled(html: str) -> bool:
    return "Display of Grades and Attendance has been disabled" in html


def parse_courses(html: str) -> list[Course]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="linkDescList")
    if table is None:
        return []
    labels = _term_labels(table)
    courses: list[Course] = []
    for row in table.find_all("tr"):
        row_id = row.get("id") or ""
        if not row_id.startswith("ccid_"):
            continue
        course_cell = row.find("td", class_="table-element-text-align-start")
        if course_cell is None:
            continue
        period_cell = row.find("td")
        period = period_cell.get_text(strip=True) if period_cell else ""
        following = course_cell.find_next_siblings("td")
        terms: list[TermGrade] = []
        extra: list[str | None] = []
        for index, cell in enumerate(following):
            label = labels[index] if index < len(labels) else f"col{index}"
            if label in {"Absences", "Tardies"} or index >= len(labels):
                extra.append(_plain(cell))
                continue
            terms.append(_term_grade(cell, label))
        absences = extra[0] if extra else None
        tardies = extra[1] if len(extra) > 1 else None
        courses.append(
            Course(
                period=period,
                name=_course_name(course_cell),
                teacher=_teacher(course_cell),
                room=_room(course_cell),
                terms=terms,
                absences=absences,
                tardies=tardies,
            )
        )
    return courses


def parse_missing_context(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(attrs={"curyearid": True, "studentrn": True})
    if node is None:
        raise ValueError("missing assignments page did not include the student year context")
    return {
        "year_id": str(node["curyearid"]),
        "student_dcid": str(node["studentrn"]),
        "school_id": str(node.get("curschoolid") or ""),
        "current_store_code": str(node.get("curfgname") or ""),
    }


def missing_from_query(payload: dict) -> list[MissingAssignment]:
    records = payload.get("record") or []
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(str(record.get("asmtid")), []).append(record)
    chosen: list[dict] = []
    for group in grouped.values():
        quarters = [record for record in group if record.get("storecode") not in ROLLUP_STORE_CODES]
        chosen.extend(quarters or group[:1])
    assignments = [_missing_assignment(record) for record in chosen]
    assignments.sort(key=lambda item: (item.due_date or "", item.course, item.name))
    return assignments


def _term_labels(table) -> list[str]:
    for row in table.find_all("tr"):
        labels = [cell.get_text(strip=True) for cell in row.find_all("th")]
        if "Course" in labels:
            return labels[labels.index("Course") + 1 :]
    return ["Q1", "Y1", "Q2", "Q3", "Q4", "Absences", "Tardies"]


def _course_name(cell) -> str:
    parts: list[str] = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        else:
            break
    return " ".join("".join(parts).replace("\xa0", " ").split())


def _teacher(cell) -> str | None:
    link = cell.find("a", href=lambda href: href and href.startswith("mailto:"))
    if link is None:
        return None
    text = link.get_text(strip=True)
    if text.lower().startswith("email "):
        text = text[6:]
    return text or None


def _room(cell) -> str | None:
    for span in cell.find_all("span"):
        if span.get_text(strip=True).endswith("Rm:"):
            room = span.find_next_sibling("span")
            if room:
                text = room.get_text(strip=True)
                return text or None
    return None


def _term_grade(cell, store_code: str) -> TermGrade:
    link = cell.find("a", href=True)
    scores_path = link["href"] if link and "scores.html" in link["href"] else None
    text = _plain(link or cell)
    if text in {None, "[ i ]", "[i]", "Not available"}:
        text = None
    return TermGrade(store_code=store_code, grade=text, scores_path=scores_path)


def _plain(node) -> str | None:
    text = " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())
    return text or None


@dataclass
class ScoredAssignment:
    assignment_id: str
    course: str
    name: str
    due_date: str | None
    store_code: str
    percent: float | None
    points: float | None
    max_points: float | None
    missing: bool
    exempt: bool
    letter: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def current_term(course: Course, today: date) -> TermGrade | None:
    """The reporting term that contains today. A quarter beats a year that also contains today."""
    matches: list[tuple[int, TermGrade]] = []
    for term in course.terms:
        span = _term_span(term)
        if span is None:
            continue
        start, end = span
        if start <= today <= end:
            matches.append(((end - start).days, term))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def parse_scores_meta(html: str) -> tuple[str | None, str | None]:
    section = re.search(r'data-sectionid="(\d+)"', html)
    frn = re.search(r"studentFRN = '(\d+)'", html)
    dcid = None
    if frn and len(frn.group(1)) > 3:
        dcid = str(int(frn.group(1)[3:]))
    return (section.group(1) if section else None, dcid)


def assignments_from_lookup(payload, course: str, store_code: str) -> list[ScoredAssignment]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    found: list[ScoredAssignment] = []
    for item in rows:
        assignment_id = item.get("assignmentid") or item.get("_id")
        for section in item.get("_assignmentsections") or []:
            scores = section.get("_assignmentscores") or []
            score = scores[0] if scores else {}
            points = _float(score.get("scorepoints"))
            max_points = _float(section.get("scoreentrypoints"))
            percent = _float(score.get("scorepercent"))
            if percent is None and points is not None and max_points:
                percent = points / max_points * 100
            found.append(
                ScoredAssignment(
                    assignment_id=str(assignment_id),
                    course=course,
                    name=str(section.get("name") or item.get("_name") or "Assignment"),
                    due_date=section.get("duedate") or None,
                    store_code=store_code,
                    percent=percent,
                    points=points,
                    max_points=max_points,
                    missing=bool(score.get("ismissing")),
                    exempt=bool(score.get("isexempt")),
                    letter=score.get("scorelettergrade") or None,
                )
            )
    unique: dict[str, ScoredAssignment] = {}
    for row in found:
        current = unique.get(row.assignment_id)
        if current is None or (current.percent is None and row.percent is not None):
            unique[row.assignment_id] = row
    return list(unique.values())


def _term_span(term: TermGrade) -> tuple[date, date] | None:
    if not term.scores_path:
        return None
    query = parse_qs(urlparse(term.scores_path).query)
    begin = (query.get("begdate") or [None])[0]
    end = (query.get("enddate") or [None])[0]
    if not begin or not end:
        return None
    return datetime.strptime(begin, "%m/%d/%Y").date(), datetime.strptime(end, "%m/%d/%Y").date()


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value) -> bool:
    return str(value).strip() in {"1", "true", "True"}


@dataclass
class AssignmentWork:
    assignment_id: str
    course: str
    name: str
    due_date: str | None
    store_code: str
    percent: float | None
    points: float | None
    max_points: float | None
    missing: bool
    letter: str | None

    def to_dict(self) -> dict:
        return asdict(self)


_QUARTER = re.compile(r"^Q(\d+)$", re.I)


def current_quarter_code(courses: list[Course], today: date) -> str | None:
    """The quarter in session anywhere on the schedule. A later quarter wins."""
    best_number = -1
    best_code = None
    for course in courses:
        for term in course.terms:
            match = _QUARTER.match(term.store_code or "")
            span = _term_span(term)
            if not match or span is None or not (span[0] <= today <= span[1]):
                continue
            number = int(match.group(1))
            if number > best_number:
                best_number = number
                best_code = term.store_code
    return best_code


def latest_quarter(course: Course, today: date, current_code: str | None = None) -> TermGrade | None:
    """The current quarter column. Year and semester grades are not used."""
    quarters: list[tuple[int, TermGrade]] = []
    for term in course.terms:
        match = _QUARTER.match(term.store_code or "")
        if match:
            quarters.append((int(match.group(1)), term))
    if not quarters:
        return None
    current: list[tuple[int, TermGrade]] = []
    started: list[tuple[int, TermGrade]] = []
    for number, term in quarters:
        span = _term_span(term)
        if span is None:
            continue
        if span[0] <= today <= span[1]:
            current.append((number, term))
        elif span[0] <= today:
            started.append((number, term))
    if current:
        current.sort(key=lambda item: item[0])
        return current[-1][1]
    wanted = (current_code or "").upper()
    if wanted.startswith("Q"):
        for _number, term in quarters:
            if term.store_code.upper() == wanted:
                return term
    pool = started or quarters
    pool.sort(key=lambda item: item[0])
    return pool[-1][1]


def current_term(course: Course, today: date) -> TermGrade | None:
    """Quarter (or other short span) that contains today. Year rows cover the same dates."""
    matches: list[tuple[int, TermGrade]] = []
    for term in course.terms:
        span = _term_span(term)
        if span and span[0] <= today <= span[1]:
            matches.append(((span[1] - span[0]).days, term))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _term_span(term: TermGrade) -> tuple[date, date] | None:
    if not term.scores_path:
        return None
    query = parse_qs(urlparse(term.scores_path).query)
    beg = (query.get("begdate") or [None])[0]
    end = (query.get("enddate") or [None])[0]
    if not beg or not end:
        return None
    return datetime.strptime(beg, "%m/%d/%Y").date(), datetime.strptime(end, "%m/%d/%Y").date()


def parse_scores_ids(html: str) -> tuple[str | None, str | None]:
    section = re.search(r'data-sectionid="(\d+)"', html)
    frn = re.search(r"studentFRN\s*=\s*'(\d+)'", html)
    dcid = None
    if frn and len(frn.group(1)) > 3 and frn.group(1)[:3].isdigit():
        dcid = str(int(frn.group(1)[3:]))
    return (section.group(1) if section else None, dcid)


def assignments_from_lookup(payload, course: str, store_code: str) -> list[AssignmentWork]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("record") or []
    else:
        rows = []
    found: list[AssignmentWork] = []
    for item in rows:
        assignment_id = str(item.get("assignmentid") or item.get("_id") or "")
        sections = item.get("_assignmentsections") or []
        if not sections:
            continue
        section = sections[0]
        scores = section.get("_assignmentscores") or []
        score = scores[0] if scores else {}
        if score.get("isexempt"):
            continue
        max_points = _float_or_none(section.get("scoreentrypoints"))
        points = _float_or_none(score.get("scorepoints")) if scores else None
        percent = _float_or_none(score.get("scorepercent")) if scores else None
        if percent is None and points is not None and max_points:
            percent = (points / max_points) * 100
        letter = (score.get("scorelettergrade") or None) if scores else None
        if letter == "":
            letter = None
        found.append(
            AssignmentWork(
                assignment_id=assignment_id,
                course=course,
                name=str(section.get("name") or ""),
                due_date=section.get("duedate") or None,
                store_code=store_code,
                percent=percent,
                points=points,
                max_points=max_points,
                missing=bool(score.get("ismissing")) if scores else False,
                letter=letter,
            )
        )
    return found


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _missing_assignment(record: dict) -> MissingAssignment:
    return MissingAssignment(
        assignment_id=str(record.get("asmtid") or ""),
        course=str(record.get("course_name") or ""),
        name=str(record.get("asmtname") or ""),
        category=record.get("catname") or None,
        due_date=record.get("duedate") or None,
        store_code=str(record.get("storecode") or ""),
        teacher=record.get("teachername") or None,
        score_type=record.get("scoretype") or None,
        missing=_flag(record.get("asmtismissing")),
        absent=_flag(record.get("asmtisabsent")),
        incomplete=_flag(record.get("asmtisincomplete")),
    )

