"""
AUC — PDF export for approved resident summaries.

Renders an approved summary into a clean one-page PDF using fpdf2 (pure
Python, no native dependencies — installs fine on the DGX Spark's ARM CPU).

The summary text is lightly-structured markdown produced by the AI prompt
(## headings, - bullets, **bold**). We parse just those few constructs into
a readable layout rather than pulling in a full markdown engine.
"""

import re
from datetime import date
from pathlib import Path

from fpdf import FPDF

# A system DejaVu font gives us full Unicode (smart quotes, em-dashes, etc.).
# It ships with most Linux distros; if it's missing we fall back to the core
# Helvetica font and transliterate non-Latin-1 characters instead.
_DEJAVU_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]
_DEJAVU_BOLD_CANDIDATES = [
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
    pdf.cell(0, 9, pdf._enc("Resident Competency Summary"), ln=True)
    pdf.set_font(fam, "B", 13)
    pdf.cell(0, 8, pdf._enc(f"Dr. {first_name} {last_name}  -  PGY-{pgy_year}"), ln=True)
    pdf.set_font(fam, "", 10)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 6, pdf._enc(f"Approved {approved_date}    Generated {date.today().isoformat()}"), ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    y = pdf.get_y()
    pdf.line(20, y, 196, y)
    pdf.ln(4)

    # --- Body ---
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
