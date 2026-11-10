"""
AUC — PDF export for approved resident summaries.

Renders an approved summary into a clean one-page PDF using fpdf2 (pure
Python, no native dependencies — installs fine on the DGX Spark's ARM CPU).

Summaries come in two shapes:

  * The current structured report (JSON, schema "auc.summary.v1" or, for one
    that covers a review period, "auc.summary.v2") — one entry per ACGME
    sub-competency with a suggested level, narrative, and validated quotes.
    Rendered directly, no markdown involved.
  * Older summaries, which were lightly-structured markdown (## headings, - bullets,
    **bold**). Still parsed by the legacy renderer so existing approved summaries
    keep exporting.

render_packet_pdf builds the advisor packet from a v2 report (see packets.py).
"""

import json
import re
from datetime import date
from pathlib import Path

from fpdf import FPDF
from summary_builder import (
    COMPETENCY_EMPTY_TEXT,
    REPORT_SCHEMAS,
    STATUS_INSUFFICIENT,
    STATUS_NO_EVIDENCE,
    STATUS_OK,
)

DRAFT_DISCLAIMER = (
    "Suggested levels are drafts for Clinical Competency Committee discussion, "
    "not final determinations."
)

# A DejaVu font gives us full Unicode (smart quotes, em-dashes, etc.). It
# ships with the app, in backend/fonts/, so a PDF looks the same on every
# machine — a Linux box without fonts-dejavu-core, a Mac, a Windows PC — rather
# than depending on what the operating system happens to have installed. The
# system paths stay as fallbacks for an install that somehow lacks the bundle;
# with neither, we fall back to the core Helvetica font and transliterate
# non-Latin-1 characters instead.
_BUNDLED_FONTS = Path(__file__).resolve().parent / "fonts"
_DEJAVU_CANDIDATES = [
    _BUNDLED_FONTS / "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]
_DEJAVU_BOLD_CANDIDATES = [
    _BUNDLED_FONTS / "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _find(paths):
    for p in paths:
        if Path(p).exists():
            return p
    return None


# Characters fpdf2's core fonts (Latin-1) can't encode, mapped to plain ASCII.
_ASCII_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", "•": "-",
    " ": " ", "−": "-", "·": "-",
}


def _to_latin1(text: str) -> str:
    for bad, good in _ASCII_MAP.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


class _SummaryPDF(FPDF):
    def __init__(self, unicode_ok: bool):
        super().__init__(format="Letter")
        self.unicode_ok = unicode_ok
        self.font_family = "DejaVu" if unicode_ok else "Helvetica"
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(20, 20, 20)

    def _enc(self, text: str) -> str:
        return text if self.unicode_ok else _to_latin1(text)


def _write_inline(pdf: _SummaryPDF, text: str, size: int):
    """Write a line, honoring **bold** spans, then line-break."""
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            pdf.set_font(pdf.font_family, "", size)
            pdf.write(6, pdf._enc(text[pos:m.start()]))
        pdf.set_font(pdf.font_family, "B", size)
        pdf.write(6, pdf._enc(m.group(1)))
        pos = m.end()
    if pos < len(text):
        pdf.set_font(pdf.font_family, "", size)
        pdf.write(6, pdf._enc(text[pos:]))
    pdf.ln(7)


def _parse_structured(summary_text: str):
    """Return the structured report dict, or None if this is a legacy markdown blob."""
    if not summary_text or not summary_text.lstrip().startswith("{"):
        return None
    try:
        report = json.loads(summary_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(report, dict) and report.get("schema") in REPORT_SCHEMAS:
        return report
    return None


_STATUS_NOTES = {
    STATUS_NO_EVIDENCE: "No evidence in this review period.",
    STATUS_INSUFFICIENT: "Insufficient evidence — draft withheld.",
}


def _para(pdf: _SummaryPDF, text: str, size: int, style: str = "",
          height: float = 6, indent: float = 0, color=(0, 0, 0), align: str = "J"):
    """Write a wrapped paragraph starting at a known x.

    fpdf2's multi_cell leaves the cursor at the cell's right edge, so every call has
    to reset x itself or the next one gets zero usable width.
    """
    x = pdf.l_margin + indent
    pdf.set_x(x)
    pdf.set_font(pdf.font_family, style, size)
    pdf.set_text_color(*color)
    pdf.multi_cell(pdf.w - pdf.r_margin - x, height, pdf._enc(text), align=align)
    pdf.set_text_color(0, 0, 0)
    pdf.set_x(pdf.l_margin)


def _render_structured(pdf: _SummaryPDF, report: dict):
    """Render the structured report: domain headings, then one block per section."""
    current_domain = None

    for section in report.get("sections") or []:
        domain_name = section.get("domain_name") or section.get("domain") or ""
        if domain_name != current_domain:
            current_domain = domain_name
            pdf.ln(3)
            _para(pdf, domain_name, 13, "B", height=7, color=(30, 60, 120))
            pdf.ln(1)

        _para(pdf, f"{section.get('id', '')} — {section.get('name', '')}", 11, "B")

        status = section.get("status")
        if status != STATUS_OK:
            _para(
                pdf, _STATUS_NOTES.get(status, "Could not generate this section."),
                10, color=(120, 120, 120),
            )
            pdf.ln(2)
            continue

        level = section.get("level")
        _para(
            pdf,
            f"Suggested level {level} — committee draft" if level
            else "No suggested level — committee draft",
            10, "B", color=(70, 90, 140),
        )

        narrative = section.get("narrative") or ""
        if narrative:
            _para(pdf, narrative, 11)

        quotes = section.get("quotes") or []
        if quotes:
            pdf.ln(1)
            _para(pdf, "Supporting evidence", 9, "B", height=5, indent=4,
                  color=(90, 90, 90))
            open_q = "“" if pdf.unicode_ok else '"'
            close_q = "”" if pdf.unicode_ok else '"'
            for q in quotes:
                _para(pdf, f"{open_q}{q.get('text', '')}{close_q}", 10,
                      height=5, indent=4)
                source = q.get("source")
                if source:
                    _para(pdf, source, 8, height=4, indent=4,
                          color=(120, 120, 120))
        pdf.ln(3)


def render_summary_pdf(
    *, first_name: str, last_name: str, pgy_year, summary_text: str,
    approved_date: str,
) -> bytes:
    """Return the PDF for one approved summary as bytes."""
    font_path = _find(_DEJAVU_CANDIDATES)
    bold_path = _find(_DEJAVU_BOLD_CANDIDATES)
    unicode_ok = bool(font_path and bold_path)

    pdf = _SummaryPDF(unicode_ok)
    if unicode_ok:
        pdf.add_font("DejaVu", "", font_path)
        pdf.add_font("DejaVu", "B", bold_path)
    pdf.add_page()

    fam = pdf.font_family

    # --- Header ---
    pdf.set_font(fam, "B", 16)
    pdf.cell(0, 9, pdf._enc("Resident Competency Summary"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "B", 13)
    pdf.cell(0, 8, pdf._enc(f"Dr. {first_name} {last_name}  -  PGY-{pgy_year}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 10)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 6, pdf._enc(f"Approved {approved_date}    Generated {date.today().isoformat()}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 9)
    pdf.multi_cell(0, 5, pdf._enc(DRAFT_DISCLAIMER))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    y = pdf.get_y()
    pdf.line(20, y, 196, y)
    pdf.ln(4)

    # --- Body ---
    report = _parse_structured(summary_text)
    if report is not None:
        if isinstance(report.get("period"), dict):
            _period_lines(pdf, report)
        _render_structured(pdf, report)
        return bytes(pdf.output())

    # Legacy markdown summaries.
    for raw in (summary_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            pdf.ln(3)
            continue
        if line.startswith("### "):
            pdf.ln(1)
            _write_inline(pdf, line[4:].strip(), 11)
        elif line.startswith("## "):
            pdf.ln(2)
            pdf.set_font(fam, "B", 13)
            pdf.set_text_color(30, 60, 120)
            pdf.multi_cell(0, 7, pdf._enc(line[3:].strip()))
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        elif line.startswith("# "):
            pdf.ln(2)
            pdf.set_font(fam, "B", 14)
            pdf.multi_cell(0, 7, pdf._enc(line[2:].strip()))
            pdf.ln(1)
        elif re.match(r"^\s*[-*]\s+", line):
            content = re.sub(r"^\s*[-*]\s+", "", line)
            pdf.set_x(24)
            pdf.set_font(fam, "", 11)
            pdf.write(6, pdf._enc("• " if pdf.unicode_ok else "- "))
            _write_inline(pdf, content, 11)
        else:
            _write_inline(pdf, line, 11)

    out = pdf.output()
    return bytes(out)


# ---------------------------------------------------------------------------
# Review period lines, for an approved summary that covers one
# ---------------------------------------------------------------------------

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def human_date(iso, short=False):
    """'2026-06-23' -> '23 June 2026' (or '23 Jun 2026')."""
    try:
        y, m, d = (int(p) for p in str(iso)[:10].split("-"))
        month = _MONTHS[m - 1]
        return f"{d} {month[:3] if short else month} {y}"
    except (ValueError, IndexError, TypeError):
        return str(iso or "")


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def counts_sentence(counts):
    counts = counts or {}
    text = (f"{_plural(counts.get('n_comments', 0), 'comment')} from "
            f"{_plural(counts.get('n_evaluators', 0), 'evaluator')} across "
            f"{_plural(counts.get('n_rotations', 0), 'rotation')}")
    if counts.get("n_late"):
        text += (f", including {counts['n_late']} that arrived after the previous review "
                 f"for rotations that ended before this period")
    return text + "."


THIN_EVIDENCE_NOTICE = (
    "Few evaluations were available for this period. Treat this summary with caution."
)


def _period_lines(pdf, report):
    period = report["period"]
    _para(pdf, f"Review period {human_date(period.get('start'))} to {human_date(period.get('end'))}"
               f" ({period.get('basis_description') or ''}).", 10, color=(70, 70, 70))
    _para(pdf, counts_sentence(report.get("counts")), 10, color=(70, 70, 70))
    if report.get("thin_evidence"):
        _para(pdf, THIN_EVIDENCE_NOTICE, 10, "B", color=(150, 90, 0))
    pdf.ln(2)


# ---------------------------------------------------------------------------
# The advisor packet
# ---------------------------------------------------------------------------

PACKET_DISCLAIMER = (
    "AI generated draft. Not reviewed or edited. Every quotation was checked word for "
    "word against the source evaluations."
)

_INK = (25, 25, 25)
_MUTED = (110, 110, 110)
_ACCENT = (30, 60, 120)
_RULE = (200, 200, 200)


class _PacketPDF(_SummaryPDF):
    """A summary PDF with a footer on every page."""

    footer_lines = ("", "")

    def __init__(self, unicode_ok):
        super().__init__(unicode_ok)
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(auto=True, margin=22)

    def _fit(self, text, room):
        while text and self.get_string_width(self._enc(text)) > room:
            text = text[:-2].rstrip() + "…"
        return text

    def footer(self):
        """Two lines: the period and cutoff with the page number, then what built it."""
        self.set_y(-15)
        self.set_draw_color(*_RULE)
        self.line(self.l_margin, self.get_y() - 1.5, self.w - self.r_margin, self.get_y() - 1.5)
        self.set_font(self.font_family, "", 7)
        self.set_text_color(*_MUTED)
        width = self.content_width
        page_w = 26
        first, second = self.footer_lines
        self.cell(width - page_w, 3.8, self._enc(self._fit(first, width - page_w - 3)), align="L")
        self.set_font(self.font_family, "B", 7)
        self.cell(page_w, 3.8, self._enc(f"Page {self.page_no()} of {{nb}}"), align="R",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_font(self.font_family, "", 7)
        self.cell(width, 3.8, self._enc(self._fit(second, width)), align="L")
        self.set_text_color(0, 0, 0)

    # -- layout helpers ------------------------------------------------------

    @property
    def content_width(self):
        return self.w - self.l_margin - self.r_margin

    def text_height(self, text, size, style="", line=5, indent=0):
        """Height a _para of this text would take, without writing it."""
        self.set_font(self.font_family, style, size)
        lines = self.multi_cell(
            self.content_width - indent, line, self._enc(text or " "),
            dry_run=True, output="LINES",
        )
        return len(lines) * line

    def need(self, height):
        """Start a new page unless `height` mm still fits on this one."""
        if self.get_y() + height > self.page_break_trigger:
            self.add_page()


_MEDHUB_LABEL = re.compile(
    r"^MedHub comment, (?P<form>.+?), evaluator=(?P<evaluator>.+?), "
    r"rotation (?P<start>\d{4}-\d{2}-\d{2}) to (?P<end>\d{4}-\d{2}-\d{2}), "
    r"service=(?P<service>.+?), question: .*?(?P<late>, arrived after the previous review)?$"
)


def packet_source(label):
    """A MedHub source label, shortened for print: evaluator, form, service, dates.

    The question is left out (it is the same comment box on every evaluation); the
    evaluator's name stays, because advisors always see it in MedHub. Anything that
    is not a MedHub comment label is printed as it is.
    """
    m = _MEDHUB_LABEL.match(label or "")
    if not m:
        return label or ""
    start, end = human_date(m["start"], True), human_date(m["end"], True)
    dates = start if start == end else f"{start} to {end}"
    text = f"{m['evaluator']}  ·  {m['form']}  ·  {m['service']}  ·  rotation {dates}"
    if m["late"]:
        text += "  ·  evaluated after the previous review"
    return text


def _quote_height(pdf, quote, indent=5):
    text, source = _quote_parts(pdf, quote)
    return pdf.text_height(text, 9.5, line=4.6, indent=indent) + (
        pdf.text_height(source, 7.5, line=3.6, indent=indent) if source else 0
    ) + 1.5


def _quote_parts(pdf, quote):
    open_q = "“" if pdf.unicode_ok else '"'
    close_q = "”" if pdf.unicode_ok else '"'
    source = packet_source(quote.get("source") or "")
    if not pdf.unicode_ok:
        source = source.replace("·", "-")
    return f"{open_q}{quote.get('text', '')}{close_q}", source


def _quote_block(pdf, quote, indent=5):
    text, source = _quote_parts(pdf, quote)
    pdf.need(_quote_height(pdf, quote, indent))
    _para(pdf, text, 9.5, height=4.6, indent=indent, color=_INK, align="L")
    if source:
        _para(pdf, source, 7.5, height=3.6, indent=indent, color=_MUTED, align="L")
    pdf.ln(1.5)


def _heading(pdf, text, size=13, keep=30, color=_ACCENT, rule=True):
    """A heading that never sits alone at the bottom of a page: `keep` mm of what
    follows must fit under it."""
    pdf.need(size * 0.6 + keep)
    _para(pdf, text, size, "B", height=size * 0.55, color=color, align="L")
    if rule:
        y = pdf.get_y() + 0.5
        pdf.set_draw_color(*_RULE)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(2)


def _notice(pdf, text, fill, ink, style="B", size=9.5):
    height = pdf.text_height(text, size, style, line=4.8, indent=6) + 4
    pdf.need(height)
    x, y = pdf.l_margin, pdf.get_y()
    pdf.set_fill_color(*fill)
    pdf.rect(x, y, pdf.content_width, height, style="F")
    pdf.set_xy(x + 3, y + 2)
    pdf.set_font(pdf.font_family, style, size)
    pdf.set_text_color(*ink)
    pdf.multi_cell(pdf.content_width - 6, 4.8, pdf._enc(text), align="L")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(pdf.l_margin, y + height + 2)


def _label_value(pdf, label, value, label_w=34):
    pdf.set_x(pdf.l_margin)
    pdf.set_font(pdf.font_family, "", 9)
    pdf.set_text_color(*_MUTED)
    pdf.cell(label_w, 5.2, pdf._enc(label))
    pdf.set_font(pdf.font_family, "B", 10)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(pdf.content_width - label_w, 5.2, pdf._enc(value), align="L")
    pdf.set_text_color(0, 0, 0)
    pdf.set_x(pdf.l_margin)


def _tally(pdf, tally):
    """MedHub milestone ratings in the period, one cell per MedHub label. Counts only."""
    from review_period import TALLY_LABELS, UNABLE_LABEL

    labels = TALLY_LABELS + [UNABLE_LABEL]
    width = pdf.content_width / len(labels)
    pdf.need(16)
    _para(pdf, "MedHub milestone ratings in this period", 7.5, height=3.6, color=_MUTED,
          align="L")
    y = pdf.get_y() + 0.5
    pdf.set_draw_color(*_RULE)
    pdf.set_fill_color(246, 247, 249)
    pdf.rect(pdf.l_margin, y, pdf.content_width, 13, style="DF")
    for i, label in enumerate(labels):
        x = pdf.l_margin + i * width
        if i:
            pdf.line(x, y, x, y + 13)
        pdf.set_xy(x + 0.5, y + 1)
        pdf.set_font(pdf.font_family, "", 6.8)
        pdf.set_text_color(*_MUTED)
        pdf.multi_cell(width - 1, 3, pdf._enc(label), align="C", max_line_height=3)
        count = (tally or {}).get(label, 0)
        pdf.set_xy(x, y + 7.4)
        pdf.set_font(pdf.font_family, "B", 10.5)
        pdf.set_text_color(*(_INK if count else (170, 170, 170)))
        pdf.cell(width, 5, str(count), align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(pdf.l_margin, y + 15)


def _statement_height(pdf, block, empty_text):
    """Title, statement and its first quote: what must stay on one page together."""
    if not block:
        return 5 + pdf.text_height(empty_text, 9.5, line=4.8) + 1.5
    quotes = block.get("quotes") or []
    return 5 + pdf.text_height(block.get("text") or "", 10, line=5) + 1 + (
        _quote_height(pdf, quotes[0]) if quotes else 0)


def _statement(pdf, title, block, empty_text):
    pdf.need(_statement_height(pdf, block, empty_text))
    _para(pdf, title, 9.5, "B", height=5, color=_ACCENT, align="L")
    if not block:
        _para(pdf, empty_text, 9.5, height=4.8, color=_MUTED, align="L")
        pdf.ln(1.5)
        return
    _para(pdf, block.get("text") or "", 10, height=5, color=_INK, align="L")
    pdf.ln(1)
    for quote in block.get("quotes") or []:
        _quote_block(pdf, quote)


_NO_STRENGTH = "Nothing in this period's evaluations could be quoted to support a strength."
_NO_GROWTH = "Nothing in this period's evaluations could be quoted to support a growth area."
_TALLY_HEIGHT = 19


def _competency(pdf, block, tally):
    name = block.get("name") or block.get("id") or ""
    ok = block.get("status") == STATUS_OK
    # The heading stays with its tally and the first statement (or the empty note).
    first = _statement_height(pdf, block.get("strengths"), _NO_STRENGTH) if ok else 8
    _heading(pdf, name, size=11.5, keep=_TALLY_HEIGHT + first, rule=False)
    _tally(pdf, tally)
    if not ok:
        note = block.get("note")
        if block.get("status") not in (STATUS_NO_EVIDENCE, STATUS_INSUFFICIENT):
            note = f"Could not generate this section ({note})." if note else \
                "Could not generate this section."
        _para(pdf, note or COMPETENCY_EMPTY_TEXT, 10, height=5, color=_MUTED, align="L")
        pdf.ln(4)
        return
    _statement(pdf, "Strengths", block.get("strengths"), _NO_STRENGTH)
    _statement(pdf, "Growth areas", block.get("growth_areas"), _NO_GROWTH)
    pdf.ln(3)


def _previously(pdf, previously):
    _heading(pdf, "Previously", keep=22)
    if not previously:
        _para(pdf, "This is the first packet for this resident.", 10, color=_MUTED, align="L")
        pdf.ln(3)
        return
    _para(pdf, f"The previous packet covered {human_date(previously.get('period_start'))} "
               f"to {human_date(previously.get('period_end'))}. What it said, in brief:",
          10, height=5, color=_INK, align="L")
    pdf.ln(1.5)
    points = previously.get("points") or []
    if not points:
        _para(pdf, "It had no statements that could be backed by quotations.", 10,
              color=_MUTED, align="L")
    for point in points:
        lines = []
        if point.get("strengths"):
            lines.append(f"Strengths: {point['strengths']}")
        if point.get("growth_areas"):
            lines.append(f"Growth areas: {point['growth_areas']}")
        height = 4.8 + sum(pdf.text_height(t, 9, line=4.4, indent=4) for t in lines) + 1.5
        pdf.need(height)
        _para(pdf, point.get("competency") or "", 9.5, "B", height=4.8, color=_INK, align="L")
        for text in lines:
            _para(pdf, text, 9, height=4.4, indent=4, color=_INK, align="L")
        pdf.ln(1.5)
    pdf.ln(2)


def _appendix(pdf, report, show_levels):
    pdf.add_page()
    _heading(pdf, "Appendix: the 21 sub-competencies", size=13, keep=30)
    _para(pdf, "The same draft, in more detail. Each section reads only the comments routed "
               "to that sub-competency." + ("" if show_levels else
                                            " Suggested levels are not printed."),
          9, height=4.6, color=_MUTED, align="L")
    pdf.ln(2)
    current = None
    for section in report.get("sections") or []:
        domain = section.get("domain_name") or section.get("domain") or ""
        if domain != current:
            current = domain
            pdf.ln(1)
            _heading(pdf, domain, size=11, keep=26, rule=False)
        status = section.get("status")
        quotes = section.get("quotes") or []
        keep = 5 + (pdf.text_height(section.get("narrative") or "", 9.5, line=4.8)
                    + (_quote_height(pdf, quotes[0]) if quotes else 0)
                    if status == STATUS_OK else 5)
        pdf.need(keep + (4.4 if show_levels else 0))
        _para(pdf, f"{section.get('id', '')} — {section.get('name', '')}", 10, "B",
              height=5, color=_INK, align="L")
        if status != STATUS_OK:
            _para(pdf, _STATUS_NOTES.get(status, "Could not generate this section."), 9,
                  height=4.6, color=_MUTED, align="L")
            pdf.ln(2)
            continue
        if show_levels:
            level = section.get("level")
            _para(pdf, f"Suggested level {level} — committee draft" if level
                  else "No suggested level — committee draft", 8.5, "B", height=4.4,
                  color=(70, 90, 140), align="L")
        _para(pdf, section.get("narrative") or "", 9.5, height=4.8, color=_INK, align="L")
        pdf.ln(1)
        for quote in section.get("quotes") or []:
            _quote_block(pdf, quote)
        pdf.ln(1.5)


def render_packet_pdf(*, packet, report, resident_name=None) -> bytes:
    """The advisor packet for one packets row and the v2 report it was built from.

    Order: header, the six competencies, previously, the 21 sub-competencies. A footer
    on every page: period, cutoff, app version, model, packet id, format, page. The
    study code is never printed.
    """
    font_path = _find(_DEJAVU_CANDIDATES)
    bold_path = _find(_DEJAVU_BOLD_CANDIDATES)
    unicode_ok = bool(font_path and bold_path)
    pdf = _PacketPDF(unicode_ok)
    if unicode_ok:
        pdf.add_font("DejaVu", "", font_path)
        pdf.add_font("DejaVu", "B", bold_path)

    period = report.get("period") or {}
    start = period.get("start") or packet.get("period_start")
    end = period.get("end") or packet.get("period_end")
    cutoff = packet.get("cutoff_date") or end
    lines = (
        f"Review period {human_date(start, True)} to {human_date(end, True)}  ·  "
        f"Cutoff date {human_date(cutoff, True)}",
        f"Packet {packet.get('id')}  ·  Packet format {packet.get('packet_format_version')}  ·  "
        f"AUC {packet.get('app_version') or 'dev'}  ·  "
        f"Model {packet.get('model') or report.get('model') or 'unknown'}",
    )
    pdf.footer_lines = lines if unicode_ok else tuple(t.replace("·", "-") for t in lines)
    pdf.set_title(f"Advisor packet {packet.get('id')}")
    pdf.add_page()

    # --- Header ---
    name = resident_name or (report.get("resident") or "").split(", PGY")[0]
    pgy = packet.get("pgy_year_at_cutoff") or report.get("pgy_year_at_cutoff")
    _para(pdf, "ADVISOR PACKET", 8, "B", height=4, color=_MUTED, align="L")
    _para(pdf, name, 17, "B", height=8, color=_INK, align="L")
    _para(pdf, f"PGY-{pgy} on the cutoff date" if pgy else "PGY year not known", 10.5,
          height=5.5, color=_INK, align="L")
    if report.get("pgy_note"):
        _para(pdf, report["pgy_note"], 8.5, height=4.2, color=(150, 90, 0), align="L")
    pdf.ln(2.5)

    _label_value(pdf, "Advisor", packet.get("advisor_name") or "Not recorded")
    _label_value(pdf, "Meeting", human_date(packet.get("meeting_date")))
    _label_value(pdf, "Review period", f"{human_date(start)} to {human_date(end)}")
    _label_value(pdf, "Cutoff date", human_date(cutoff))
    if period.get("basis_description"):
        _label_value(pdf, "Period starts", period["basis_description"].replace(
            "Starts the day after", "The day after").replace("Starts at", "At"))
    _label_value(pdf, "Evidence", counts_sentence(report.get("counts")))
    pdf.ln(2)
    _notice(pdf, PACKET_DISCLAIMER, fill=(235, 240, 250), ink=_ACCENT)
    if report.get("thin_evidence"):
        _notice(pdf, THIN_EVIDENCE_NOTICE, fill=(253, 243, 224), ink=(140, 80, 0))
    pdf.ln(1)

    # --- The six competencies ---
    _heading(pdf, "The six competencies", keep=50)
    tallies = report.get("rating_tallies") or {}
    for block in report.get("competencies") or []:
        _competency(pdf, block, tallies.get(block.get("id")))

    # --- Previously ---
    _previously(pdf, report.get("previously"))

    # --- Appendix ---
    _appendix(pdf, report, bool(packet.get("show_levels")))
    return bytes(pdf.output())
