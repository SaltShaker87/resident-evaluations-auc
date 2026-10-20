"""
AUC — MedHub report reader.

Reads MedHub's "Evaluation Questions Summary" report, saved from the browser as
an HTML page. Pure parsing: nothing here touches the database, so the import
endpoints (medhub_import.py) and the tests share exactly one reading of a file.

The report, as MedHub lays it out:

    <p>Evaluation: <strong>{form}</strong><br>Date Range: 07/01/26-10/19/26</p>
    <strong>{resident}</strong>                     one section per resident
    <table>                                          one table per question
      <tr><td colspan=5><em>Question: {question}*</em></td></tr>
      <tr><th>Target:</th><th>Evaluator:</th><th>Rotation Dates:</th>
          <th>Service:</th><th>Answer:</th></tr>
      <tr><td>{resident}</td><td>{evaluator}</td><td>9/14/2026-9/20/2026</td>
          <td>{service}</td><td>{answer}</td></tr>   one per evaluation
      <tr><td> <br></td></tr>                        spacer
    </table>

Every row returned is one evaluator's answer to one question. A file may hold
more than one form; nothing here assumes there is only one.

Names: MedHub writes residents as "Last, First" and sometimes prefixes "Dr.".
A name is always compared as one whole string ("Abiddin, Zain Ul" is one name,
not a last name plus two first names) after removing that prefix and tidying
spaces and letter case. Nothing is ever matched on part of a name.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import List, Optional, Tuple

EVALUATION_QUESTIONS_SUMMARY = "Evaluation Questions Summary"

# The inpatient milestone scale. MedHub sometimes adds a description in
# brackets ("Intern (advanced beginner)"); the bracket is ignored.
MILESTONE_LEVELS = {
    "conditional on improvement": 1,
    "intern": 2,
    "junior resident": 3,
    "senior resident": 4,
    "aspirational": 5,
}
UNABLE_TO_ASSESS = "unable to assess"

_EXPECTED_COLUMNS = ("target", "evaluator", "rotation dates", "service", "answer")


class MedhubReportError(ValueError):
    """The file is not a report AUC can read. The message is shown to the user."""


@dataclass(frozen=True)
class AnswerRow:
    form: str
    resident: str          # as printed in the Target column
    evaluator: str
    rotation_start: str    # ISO date
    rotation_end: str      # ISO date — the date used for graphs
    service: str
    question: str
    answer: str


@dataclass
class FormSection:
    name: str
    date_range: Optional[str] = None   # as printed, e.g. "07/01/26-10/19/26"


@dataclass
class Report:
    report_type: str
    generated: Optional[str]
    forms: List[FormSection] = field(default_factory=list)
    rows: List[AnswerRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text and name normalisation
# ---------------------------------------------------------------------------

_DR_PREFIX = re.compile(r"^dr(?:\.\s*|\s+)", re.IGNORECASE)


def clean_text(raw: str) -> str:
    """Tidy a cell's text. MedHub hard-wraps long comments at about 70
    characters, so a single line break is layout and becomes a space; a blank
    line is a real paragraph break and is kept."""
    text = unicodedata.normalize("NFC", raw.replace("\xa0", " ").replace("\r", ""))
    paragraphs = re.split(r"\n\s*\n", text)
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def display_name(raw: str) -> str:
    """A MedHub name without its "Dr." prefix and with tidy spacing,
    e.g. "Dr.  Abiddin ,Zain Ul" -> "Abiddin, Zain Ul"."""
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    name = _DR_PREFIX.sub("", name)
    return re.sub(r"\s*,\s*", ", ", name).strip()


def name_key(raw: str) -> str:
    """The form two names are compared in: whole string, case-insensitive."""
    return display_name(raw).casefold()


def clean_question(raw: str) -> str:
    """Question text without the "Question:" label or the trailing "*" MedHub
    puts on required questions, so marking a question required later does not
    make it a different question."""
    text = " ".join(clean_text(raw).split())
    if text.lower().startswith("question:"):
        text = text[len("question:"):]
    return text.strip().rstrip("*").strip()


def fingerprint(row: AnswerRow) -> str:
    """Identifies one evaluator's answer to one question for one rotation.

    resident + evaluator + form + rotation dates + question — the same for a
    rating and a comment. The answer itself is deliberately left out: the same
    evaluation exported twice must give the same fingerprint even if MedHub
    wraps the text differently, and a changed answer is then recognised as a
    change rather than a second evaluation.
    """
    parts = [
        "v1",
        name_key(row.resident),
        name_key(row.evaluator),
        " ".join(row.form.split()).casefold(),
        row.rotation_start,
        row.rotation_end,
        " ".join(row.question.split()).casefold(),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def milestone_level(answer: str) -> Tuple[str, Optional[int]]:
    """Classify an answer on the milestone scale.

    Returns (status, level): ("rated", 1-5), ("unable", None) for "Unable to
    Assess", ("blank", None), or ("unrecognised", None) for anything else.
    """
    key = re.sub(r"\s*\(.*\)\s*$", "", answer.strip()).strip().rstrip(".")
    key = " ".join(key.split()).casefold()
    if not key:
        return "blank", None
    if key in MILESTONE_LEVELS:
        return "rated", MILESTONE_LEVELS[key]
    if key == UNABLE_TO_ASSESS:
        return "unable", None
    return "unrecognised", None


def comment_kind(answer: str) -> str:
    """"text", "blank", or "na". N/A is an option evaluators pick on purpose,
    so it is counted apart from a comment left empty."""
    text = answer.strip()
    if not text:
        return "blank"
    if text.rstrip(".").strip().casefold() == "n/a":
        return "na"
    return "text"


def looks_like_milestone(answers) -> bool:
    """True when every non-blank answer is on the milestone scale. Used only to
    pre-select the question type in the import preview; the user confirms it."""
    statuses = [milestone_level(a)[0] for a in answers]
    non_blank = [s for s in statuses if s != "blank"]
    return bool(non_blank) and all(s in ("rated", "unable") for s in non_blank)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

class _Flattener(HTMLParser):
    """Reduces the page to the few things the report is made of: the page
    title, text and <strong> runs outside tables, and table rows as lists of
    cell texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.events: list = []
        self._in_title = False
        self._skip_depth = 0
        self._table_depth = 0
        self._row: Optional[list] = None
        self._row_is_header = False
        self._cell: Optional[list] = None
        self._strong: Optional[list] = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "table":
            self._table_depth += 1
        elif tag == "tr" and self._table_depth:
            self._end_row()
            self._row, self._row_is_header = [], False
        elif tag in ("td", "th") and self._table_depth:
            self._end_cell()
            if self._row is None:
                self._row, self._row_is_header = [], False
            self._row_is_header = self._row_is_header or tag == "th"
            self._cell = []
        elif tag == "br":
            self._text("\n")
        elif tag == "strong" and not self._table_depth:
            self._strong = []

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._text("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th"):
            self._end_cell()
        elif tag == "tr":
            self._end_row()
        elif tag == "table":
            self._end_row()
            self._table_depth = max(0, self._table_depth - 1)
        elif tag == "strong" and self._strong is not None:
            self.events.append(("strong", clean_text("".join(self._strong))))
            self._strong = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        else:
            self._text(data)

    def close(self):
        super().close()
        self._end_row()

    def _text(self, data):
        if self._skip_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._strong is not None:
            self._strong.append(data)
        elif not self._table_depth:
            self.events.append(("text", data))

    def _end_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell))
        self._cell = None

    def _end_row(self):
        self._end_cell()
        if self._row is not None:
            self.events.append(("row", self._row, self._row_is_header))
        self._row = None


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})"
_ROTATION = re.compile(rf"^{_DATE}(?:\s*-\s*{_DATE})?$")


def _iso(month: str, day: str, year: str) -> str:
    y = int(year)
    if y < 100:
        y += 2000
    return date(y, int(month), int(day)).isoformat()


def parse_rotation_dates(text: str) -> Tuple[str, str]:
    """"9/14/2026-9/20/2026" -> ("2026-09-14", "2026-09-20"). A single date
    is a one-day rotation."""
    match = _ROTATION.match(" ".join(text.split()))
    if not match:
        raise ValueError(text)
    start = _iso(*match.group(1, 2, 3))
    end = _iso(*match.group(4, 5, 6)) if match.group(4) else start
    return start, end


def read_report(data: bytes) -> Report:
    """Read an Evaluation Questions Summary. Raises MedhubReportError with a
    message meant for the user if the file is anything else, or if its layout
    is not one this reader knows — never a silent partial read."""
    parser = _Flattener()
    parser.feed(_decode(data))
    parser.close()

    title = " ".join(parser.title.split())
    page_text = " ".join("".join(e[1] for e in parser.events if e[0] == "text").split())
    heading = f"{title} {page_text[:3000]}"
    if EVALUATION_QUESTIONS_SUMMARY not in heading:
        if "Aggregate Comments Report" in heading:
            raise MedhubReportError(
                "This is an Aggregate Comments Report. AUC reads comments from the "
                "Evaluation Questions Summary report instead, which has them along "
                "with the scores."
            )
        raise MedhubReportError(
            "This file is not a MedHub Evaluation Questions Summary. In MedHub, open "
            "that report and save the page as HTML."
        )

    report = Report(report_type=EVALUATION_QUESTIONS_SUMMARY, generated=None)
    generated = re.search(r"Generated:\s*([^<\n]+?(?:am|pm)(?:\s+[A-Z]{2,4})?)\b", page_text)
    if generated:
        report.generated = generated.group(1).strip()

    form: Optional[FormSection] = None
    question: Optional[str] = None
    columns: Optional[list] = None
    recent_text = ""

    for event in parser.events:
        kind = event[0]
        if kind == "text":
            recent_text += event[1]
            if form is not None and form.date_range is None:
                found = re.search(r"Date Range:\s*(\S+\s*-\s*\S+)", recent_text)
                if found:
                    form.date_range = "".join(found.group(1).split())
            continue

        if kind == "strong":
            if recent_text.strip().endswith("Evaluation:"):
                form = FormSection(name=" ".join(event[1].split()))
                report.forms.append(form)
                question, columns = None, None
            # Any other <strong> is a resident's section heading. The rows
            # carry the resident's name themselves, so the heading is not needed.
            recent_text = ""
            continue

        cells, is_header = event[1], event[2]
        if len(cells) == 1 and clean_text(cells[0]).lower().startswith("question:"):
            question, columns = clean_question(cells[0]), None
            continue
        if is_header:
            columns = [clean_text(c).rstrip(":").strip().lower() for c in cells]
            missing = [c for c in _EXPECTED_COLUMNS if c not in columns]
            if missing:
                raise MedhubReportError(
                    "The report's columns are not the ones AUC expects "
                    f"(missing: {', '.join(missing)}). MedHub may have changed the "
                    "report layout."
                )
            continue
        if not any(clean_text(c) for c in cells):
            continue   # spacer row
        if form is None or question is None or columns is None or len(cells) != len(columns):
            raise MedhubReportError(
                "Part of this report is laid out in a way AUC does not recognise "
                f"(near question: {question or 'before the first question'}). "
                "Nothing was imported."
            )

        values = dict(zip(columns, cells, strict=True))
        resident = display_name(clean_text(values["target"]))
        evaluator = display_name(clean_text(values["evaluator"]))
        rotation = clean_text(values["rotation dates"])
        try:
            start, end = parse_rotation_dates(rotation)
        except ValueError:
            raise MedhubReportError(
                f'The rotation dates "{rotation}" for {resident} could not be read. '
                "Nothing was imported."
            ) from None

        report.rows.append(AnswerRow(
            form=form.name,
            resident=resident,
            evaluator=evaluator,
            rotation_start=start,
            rotation_end=end,
            service=clean_text(values["service"]),
            question=question,
            answer=clean_text(values["answer"]),
        ))

    return report
