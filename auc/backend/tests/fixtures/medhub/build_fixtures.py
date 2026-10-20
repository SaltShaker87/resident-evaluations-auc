"""
Writes the look-alike MedHub reports the import tests read.

Every person, comment and answer here is invented. The markup copies what
MedHub produces for an Evaluation Questions Summary saved from the browser —
the same tags, the same spacer rows, the same page break before the last
question, raw line breaks inside comments and &#039;-style escapes — minus the
saved page's session token and the hospital's MedHub addresses.

The expected values the tests check are written out separately in
test_medhub_import.py, not derived from this file.

Run from the repository root to regenerate:
    python auc/backend/tests/fixtures/medhub/build_fixtures.py
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

INPATIENT = "Inpatient Medicine - Attending Evaluation of Trainee"
SUBSPECIALTY = "Subspecialty Clinic - Attending Evaluation of Trainee"

INPATIENT_QUESTIONS = [
    "Resident gathers a complete history and examines patients carefully. Keeps the "
    "medication list accurate.*",
    "Resident builds a sound differential diagnosis and management plan.*",
    "Resident works with patients and families on decisions.<br>Hands off patients safely.*",
    "Resident asks for feedback and changes practice in response.*",
    "Resident communicates clearly with the team",
    "Please provide comments or specific examples regarding the resident's strengths.",
    "Please provide actionable feedback regarding areas for improvement for this resident.*",
]

SUBSPECIALTY_QUESTIONS = [
    "The resident is punctual and responsive.*",
    "The resident shows appropriate medical knowledge.*",
    "What stood out about the resident's performance during this rotation?*",
    "Are there any specific areas where the resident could improve?*",
]


def _escape(text: str) -> str:
    """MedHub's escaping: &, <, >, " and ' (as &#039;). Line breaks stay raw."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#039;"))


def _answer_cell(answer: str, rated: bool) -> str:
    # Rated answers end in <br> in MedHub's output; free text does not.
    return _escape(answer) + ("<br>" if rated else "")


def _page(sections_html: str, generated: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    <title>Evaluation Questions Summary</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script>
      if (window.location.href.indexOf('report_auth_token') !== -1) {{
        window.location.replace(window.location.href.split('?')[0]);
      }}
    </script>
</head>
<body>
<div class="container-fluid reports-view-outer">
        <nav class="navbar navbar-default navbar-fixed-top no-print">
        <div class="navbar-form navbar-left">
            <a aria-label="Return to Report Options" href="#" class="btn btn-secondary navbar-btn">
                <i class="fa fa-chevron-left"></i> Return to Options
            </a>
            <a aria-label="View List of All Reports" href="#" class="btn btn-secondary navbar-btn">
                Reports List
            </a>
        </div>
    </nav>
    <div class="row">
        <div class="col-12">
            <h3 id="site-name">
                <img src="logo.gif" style="vertical-align: bottom" alt="logo">
                - Example Medical Center
            </h3>
        </div>
    </div>
    <div class="row">
        <div class="col-12">
            <h4 id="title">Evaluation Questions Summary</h4>
            <em>Generated: {generated}</em>
        </div>
    </div>
    {sections_html}<br><br>

    </div>


</body></html>
"""


def _form(form: str, date_range: str, residents: list, questions: list, rated: set) -> str:
    """residents: [(heading name, target name, [evaluation, ...])], where an
    evaluation is (evaluator, rotation dates, service, [answer per question])."""
    out = [
        f"<p>Evaluation: <strong>{_escape(form)}</strong><br>\n"
        f"Date Range: {date_range}<br>\n<br>\n</p>"
    ]
    for heading, target, evaluations in residents:
        out.append(
            f'<strong>{_escape(heading)}</strong><br><table class="table table-condensed '
            'table-striped" style="width: 100%"></table>'
        )
        for qi, question in enumerate(questions):
            if qi == len(questions) - 1:
                out.append('<p style="page-break-after:always;"><br style="height:0px;line-height:0px;"></p>')
            rows = [
                '<table cellspacing="2" cellpadding="2" border="0" width="100%"><tbody>'
                '<tr><td class="text2" style="border:1px #A0A0A0 solid;" colspan="5">'
                f"<em>Question: {question}</em></td></tr>\n"
                "<tr><th>Target:</th><th>Evaluator:</th><th>Rotation Dates:</th>"
                "<th>Service:</th><th>Answer:</th></tr>\n"
            ]
            for evaluator, rotation, service, answers in evaluations:
                rows.append(
                    f"<tr><td>{_escape(target)}</td><td>{_escape(evaluator)}</td>"
                    f"<td>{rotation}</td><td>{_escape(service)}</td>"
                    f"<td>{_answer_cell(answers[qi], qi in rated)}</td></tr>\n"
                )
            rows.append("<tr><td> <br></td></tr>\n\n\n</tbody></table>")
            out.append("".join(rows))
    return "".join(out)


INPATIENT_RATED = {0, 1, 2, 3, 4}
SUBSPECIALTY_RATED = {0, 1}

# --- July–September: the first export -------------------------------------

HADDAD_JUL = (
    "Dr. Alvarez, Rosa", "7/6/2026-7/19/2026", "WARDS1",
    ["Intern (advanced beginner)", "Junior Resident (competent learner)", "Intern",
     "Unable to Assess", "Conditional on improvement (novice)",
     "Thorough histories and follows up on every \nlab result before rounds.",
     ""],
)
HADDAD_SEP = (
    "Dr. Brennan, Tom", "9/14/2026-9/20/2026", "ICU1",
    ["Junior Resident (competent learner)", "Junior Resident (competent learner)",
     "Senior Resident (proficient learner)", "Aspirational",
     "Junior Resident (competent learner)",
     'Calm under pressure & kind to families. Said "let\'s pause" before a hard \n'
     "conversation, which helped.",
     "N/A"],
)
VAN_DER_BERG_AUG = (
    "Dr. Chen, Wei Ling", "8/3/2026-8/16/2026", "WARDS2",
    ["Senior Resident (proficient learner)", "Senior Resident (proficient learner)",
     "Aspirational", "Senior Resident (proficient learner)", "Meets expectations",
     "Leads rounds well.",
     "Delegate more to the intern.\n\nNotes could be shorter."],
)
LINDQVIST_JUL = (
    "Dr. Alvarez, Rosa", "7/20/2026-8/2/2026", "WARDS1",
    ["Senior Resident (proficient learner)"] * 5 + ["Excellent.", ""],
)
HADDAD_NOOR_SEP = (
    "Dr. Brennan, Tom", "9/1/2026-9/7/2026", "NIGHTS",
    ["Intern (advanced beginner)"] * 5 + ["Good start.", "Read more."],
)

# --- September–October: an overlapping export -----------------------------

# The same evaluation as HADDAD_SEP, except the first answer was changed in
# MedHub after the first export.
HADDAD_SEP_CHANGED = (
    HADDAD_SEP[0], HADDAD_SEP[1], HADDAD_SEP[2],
    ["Senior Resident (proficient learner)"] + HADDAD_SEP[3][1:],
)
HADDAD_OCT = (
    "Dr. Alvarez, Rosa", "10/5/2026-10/18/2026", "WARDS1",
    ["Senior Resident (proficient learner)", "Junior Resident (competent learner)",
     "Senior Resident (proficient learner)", "Senior Resident (proficient learner)",
     "Aspirational",
     "Ran the team on her own for a week.",
     "Keep reading around each case."],
)
OKAFOR_OCT = (
    "Dr. Chen, Wei Ling", "10/12/2026-10/18/2026", "ICU2",
    ["Intern (advanced beginner)", "Unable to Assess", "Intern (advanced beginner)",
     "Junior Resident (competent learner)", "Intern (advanced beginner)",
     "Eager to learn.",
     "n/a"],
)

# --- Subspecialty clinic: a second form with its own scale -----------------

VAN_DER_BERG_CLINIC = (
    "Dr. Dube, Thandi", "10/5/2026-10/9/2026", "Y",
    ["To a great extent", "To some extent", "Curious and engaged in clinic.", "N/A"],
)


def build():
    files = {
        "eqs_inpatient_jul_sep.html": _page(
            _form(INPATIENT, "07/01/26-09/30/26", [
                ("Haddad, Noor Ul Ain", "Haddad, Noor Ul Ain", [HADDAD_JUL, HADDAD_SEP]),
                ("Van der Berg, Anna", "Van der Berg, Anna", [VAN_DER_BERG_AUG]),
                ("Lindqvist, Erik", "Lindqvist, Erik", [LINDQVIST_JUL]),
                ("Haddad, Noor", "Haddad, Noor", [HADDAD_NOOR_SEP]),
            ], INPATIENT_QUESTIONS, INPATIENT_RATED),
            "10/01/2026 8:15am EDT",
        ),
        "eqs_inpatient_sep_oct.html": _page(
            _form(INPATIENT, "09/01/26-10/19/26", [
                ("Haddad, Noor Ul Ain", "Haddad, Noor Ul Ain", [HADDAD_SEP_CHANGED, HADDAD_OCT]),
                ("Okafor, Chidi", "Dr. Okafor, Chidi", [OKAFOR_OCT]),
                ("Haddad, Noor", "Haddad, Noor", [HADDAD_NOOR_SEP]),
            ], INPATIENT_QUESTIONS, INPATIENT_RATED),
            "10/19/2026 9:11pm EDT",
        ),
        "eqs_subspecialty.html": _page(
            _form(SUBSPECIALTY, "07/01/26-10/19/26", [
                ("Van der Berg, Anna", "Van der Berg, Anna", [VAN_DER_BERG_CLINIC]),
            ], SUBSPECIALTY_QUESTIONS, SUBSPECIALTY_RATED),
            "10/19/2026 9:30pm EDT",
        ),
        "eqs_two_forms.html": _page(
            _form(INPATIENT, "10/01/26-10/19/26", [
                ("Haddad, Noor Ul Ain", "Haddad, Noor Ul Ain", [HADDAD_OCT]),
            ], INPATIENT_QUESTIONS, INPATIENT_RATED)
            + _form(SUBSPECIALTY, "10/01/26-10/19/26", [
                ("Van der Berg, Anna", "Van der Berg, Anna", [VAN_DER_BERG_CLINIC]),
            ], SUBSPECIALTY_QUESTIONS, SUBSPECIALTY_RATED),
            "10/19/2026 9:45pm EDT",
        ),
        # What MedHub gives back for a form with nothing in the date range.
        "eqs_empty.html": _page(
            f"<p>Evaluation: <strong>{_escape(INPATIENT)}</strong><br>\n"
            "Date Range: 07/01/26-10/19/26<br>\n<br>\n</p>",
            "10/19/2026 9:50pm EDT",
        ),
    }
    for name, html in files.items():
        (HERE / name).write_text(html, encoding="utf-8", newline="\n")
    return sorted(files)


if __name__ == "__main__":
    for name in build():
        print("wrote", name)
