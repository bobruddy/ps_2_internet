import unittest

from datetime import date

from ps_internet.parse import (
    Course,
    TermGrade,
    assignment_window,
    assignments_from_lookup,
    current_quarter_code,
    current_term,
    latest_quarter,
    quarter_code_for_date,
    scores_term,
    missing_from_query,
    parse_courses,
    parse_missing_context,
    parse_students,
)

HOME = """
<html><body>
<script>var studentName = 'Example, Ada Lane';</script>
<ul id="students-list">
  <li class="selected"><a href="javascript:switchStudent(10);">Ada</a></li>
  <li><a href="javascript:switchStudent(11);">Bea</a></li>
</ul>
<div id="print-school">Example District<br><span>Example High School</span></div>
<table class="linkDescList grid">
<tr class="center th2">
<th>Exp</th><th>Last Week</th><th>This Week</th><th>Course</th>
<th>Q1</th><th>Y1</th><th>Q2</th><th>Absences</th><th>Tardies</th>
</tr>
<tr class="center" id="ccid_1">
<td>4(A-D)</td>
<td>.</td><td>.</td>
<td class="table-element-text-align-start">History 9&nbsp;<br>
  <a href="mailto:teacher@example.com">Email Teacher, Sam</a>
  <span class="display-flex"><span>-&nbsp;Rm:</span><span>&nbsp;12</span></span>
</td>
<td><a href="scores.html?frn=0041&amp;fg=Q1" class="bold">C<br>75</a></td>
<td><a href="scores.html?frn=0041&amp;fg=Y1">[ i ]</a></td>
<td><a href="scores.html?frn=0041&amp;fg=Q2" class="bold">B<br>88</a></td>
<td>1</td>
<td>0</td>
</tr>
</table>
</body></html>
"""

MISSING_PAGE = """
<div curyearid="10" studentrn="1001" curschoolid="200" curfgname="Q2"></div>
"""


class ParseTests(unittest.TestCase):
    def test_students(self):
        students = parse_students(HOME)
        self.assertEqual([student.first_name for student in students], ["Ada", "Bea"])
        self.assertEqual(students[0].full_name, "Example, Ada Lane")
        self.assertIsNone(students[1].full_name)
        self.assertTrue(students[0].selected)

    def test_course_row(self):
        course = parse_courses(HOME)[0]
        self.assertEqual(course.name, "History 9")
        self.assertEqual(course.teacher, "Teacher, Sam")
        self.assertEqual(course.room, "12")
        by_code = {term.store_code: term for term in course.terms}
        self.assertEqual(by_code["Q1"].grade, "C 75")
        self.assertIsNone(by_code["Y1"].grade)
        self.assertEqual(by_code["Q2"].grade, "B 88")
        self.assertIn("fg=Q2", by_code["Q2"].scores_path)
        self.assertEqual(course.absences, "1")

    def test_missing_context_and_dedupe(self):
        context = parse_missing_context(MISSING_PAGE)
        self.assertEqual(context["student_dcid"], "1001")
        rows = missing_from_query(
            {
                "record": [
                    {"asmtid": "9", "course_name": "History", "asmtname": "Outline", "storecode": "Y1",
                     "duedate": "2026-11-02", "asmtismissing": "1", "asmtisabsent": "0", "asmtisincomplete": "0"},
                    {"asmtid": "9", "course_name": "History", "asmtname": "Outline", "storecode": "Q2",
                     "duedate": "2026-11-02", "asmtismissing": "1", "asmtisabsent": "0", "asmtisincomplete": "0"},
                ]
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].store_code, "Q2")
        self.assertTrue(rows[0].missing)

    def test_current_term_prefers_the_shorter_span(self):
        course = Course(
            period="4",
            name="History",
            teacher=None,
            room=None,
            terms=[
                TermGrade("Y1", "C 70", "scores.html?begdate=09/01/2026&enddate=06/01/2027&fg=Y1"),
                TermGrade("Q2", "B 88", "scores.html?begdate=10/16/2026&enddate=01/15/2027&fg=Q2"),
            ],
            absences=None,
            tardies=None,
        )
        term = current_term(course, date(2026, 11, 6))
        self.assertEqual(term.store_code, "Q2")

    def test_latest_quarter_ignores_the_year_grade(self):
        course = Course(
            period="6",
            name="Wood",
            teacher=None,
            room=None,
            terms=[
                TermGrade("Q1", "A 95", None),
                TermGrade("Q2", None, None),
                TermGrade("Y1", "A 90", "scores.html?begdate=09/01/2026&enddate=06/01/2027&fg=Y1"),
            ],
            absences=None,
            tardies=None,
        )
        term = latest_quarter(course, date(2026, 11, 6), "Q2")
        self.assertEqual(term.store_code, "Q2")
        self.assertIsNone(term.grade)
        dated = Course(
            period="4",
            name="History",
            teacher=None,
            room=None,
            terms=[
                TermGrade("Q1", "C 75", "scores.html?begdate=09/01/2026&enddate=10/15/2026&fg=Q1"),
                TermGrade("Q2", "B 88", "scores.html?begdate=10/16/2026&enddate=01/15/2027&fg=Q2"),
                TermGrade("Y1", "C 70", "scores.html?begdate=09/01/2026&enddate=06/01/2027&fg=Y1"),
            ],
            absences=None,
            tardies=None,
        )
        self.assertEqual(latest_quarter(dated, date(2026, 11, 6), "Q1").store_code, "Q2")
        self.assertEqual(current_quarter_code([dated, course], date(2026, 11, 6)), "Q2")
        self.assertEqual(assignment_window(dated), (date(2026, 9, 1), date(2027, 6, 1)))
        self.assertEqual(scores_term(dated).store_code, "Y1")
        self.assertEqual(quarter_code_for_date(dated, date(2026, 9, 15)), "Q1")
        self.assertEqual(quarter_code_for_date(dated, date(2026, 11, 6)), "Q2")
        self.assertEqual(quarter_code_for_date(dated, date(2026, 8, 20)), "Q1")
        quarter_only = Course(
            "2",
            "English",
            None,
            None,
            [TermGrade("Q2", "A 95", "scores.html?begdate=11/01/2026&enddate=01/20/2027&fg=Q2")],
            None,
            None,
        )
        self.assertEqual(assignment_window(quarter_only), (date(2026, 11, 1), date(2027, 1, 20)))
        self.assertEqual(scores_term(quarter_only).store_code, "Q2")
        self.assertIsNone(latest_quarter(Course("1", "Only year", None, None, [TermGrade("Y1", "A 90", None)], None, None), date(2026, 11, 6), "Y1"))

    def test_assignment_lookup_flags(self):
        rows = assignments_from_lookup(
            [
                {
                    "assignmentid": 9,
                    "_assignmentsections": [
                        {
                            "name": "Outline",
                            "duedate": "2026-11-02",
                            "scoreentrypoints": 5,
                            "_assignmentscores": [
                                {"ismissing": True, "isexempt": False, "scorepoints": 0, "scorepercent": 0}
                            ],
                        }
                    ],
                }
            ],
            "History",
            "Q2",
        )
        self.assertEqual(rows[0].assignment_id, "9")
        self.assertTrue(rows[0].missing)
        self.assertEqual(rows[0].percent, 0)


if __name__ == "__main__":
    unittest.main()
