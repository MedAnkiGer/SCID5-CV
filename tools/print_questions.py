"""
tools/print_questions.py

Generates a structured reference PDF of all SCID-5-CV questions,
including interviewer text, DSM-5 criteria, rating options, and
branching/skip logic.

Usage:
    python tools/print_questions.py [--out PATH]
    python tools/print_questions.py --out docs/SCID5CV_questions.pdf
"""

import argparse
import json
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).parent.parent
QUESTIONS_PATH = ROOT / "data" / "questions.json"
DEFAULT_OUT = ROOT / "docs" / "SCID5CV_questions_reference.pdf"

# ── colour palette (R, G, B) ──────────────────────────────────────────────
COL_MODULE_BG   = (30,  80, 140)   # dark blue — module header bar
COL_MODULE_FG   = (255, 255, 255)  # white text on module header
COL_QID_BG      = (220, 230, 245)  # light blue — question ID band
COL_BRANCH_BG   = (255, 243, 205)  # warm yellow — branching logic
COL_BRANCH_TEXT = (120,  80,   0)  # dark amber
COL_CRIT_BG     = (240, 240, 240)  # light grey — criterion description
COL_NOTE_TEXT   = (150,  50,  50)  # dark red — notes / warnings
COL_LINE        = (180, 180, 180)  # separator lines


def safe(text) -> str:
    """Encode to Latin-1, replacing unmappable characters."""
    if not text:
        return ""
    text = str(text)
    subs = {
        "\u2014": "--", "\u2013": "-", "\u2012": "-",
        "\u201c": '"',  "\u201d": '"',
        "\u2018": "'",  "\u2019": "'",
        "\u2026": "...",
        "\u2192": "->", "\u2190": "<-",
        "\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue",
        "\u00c4": "Ae", "\u00d6": "Oe", "\u00dc": "Ue",
        "\u00df": "ss",
    }
    for old, new in subs.items():
        text = text.replace(old, new)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class QRef(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=18)
        self._current_module = ""

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f"SCID-5-CV Question Reference  |  Module {self._current_module}",
                  align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 5, f"Page {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)

    # ── layout helpers ────────────────────────────────────────────────────

    def module_header(self, module_id: str, module_name: str, n_questions: int):
        """Full-width coloured bar for a module."""
        self._current_module = module_id
        self.set_fill_color(*COL_MODULE_BG)
        self.set_text_color(*COL_MODULE_FG)
        self.set_font("Helvetica", "B", 12)
        label = safe(f"Module {module_id}:  {module_name}  ({n_questions} questions)")
        self.cell(0, 10, label, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def question_id_band(self, qid: str, label: str, has_criterion: bool):
        """Light-blue band showing question ID and criterion label."""
        self.set_fill_color(*COL_QID_BG)
        self.set_font("Helvetica", "B", 9)
        tag = "" if has_criterion else "  [record only]"
        self.cell(0, 7, safe(f"  {qid}  --  {label}{tag}"),
                  fill=True, new_x="LMARGIN", new_y="NEXT")

    def interviewer_block(self, text: str, prefix: str = ""):
        """Indented interviewer question text."""
        if not text:
            return
        self.set_font("Helvetica", "", 9)
        full = (f"{prefix}  " if prefix else "") + text
        self.set_x(self.l_margin + 4)
        self.multi_cell(0, 5, safe(full), new_x="LMARGIN", new_y="NEXT")

    def criterion_box(self, text: str):
        """Grey box with DSM-5 criterion description."""
        if not text:
            return
        self.set_fill_color(*COL_CRIT_BG)
        self.set_font("Helvetica", "I", 8)
        self.set_x(self.l_margin + 4)
        self.multi_cell(0, 4, safe(f"DSM-5: {text}"),
                        fill=True, new_x="LMARGIN", new_y="NEXT")

    def rating_row(self, rating_options):
        """Compact rating options in one line."""
        if not rating_options:
            return
        if isinstance(rating_options, dict):
            parts = [f"{k} = {v}" for k, v in rating_options.items()]
        else:
            parts = [str(rating_options)]
        self.set_font("Helvetica", "B", 8)
        self.set_x(self.l_margin + 4)
        self.cell(18, 5, "Rating:", new_x="RIGHT", new_y="TOP")
        self.set_font("Helvetica", "", 8)
        self.multi_cell(0, 5, safe("   |   ".join(parts)),
                        new_x="LMARGIN", new_y="NEXT")

    def branch_box(self, skip_if: dict | str | None, notes: str | None):
        """Yellow box for branching / skip logic."""
        lines = []
        if isinstance(skip_if, dict):
            for score, target in skip_if.items():
                lines.append(f"If {score}  ->  go to {target}")
        elif isinstance(skip_if, str) and skip_if.strip():
            lines.append(skip_if.strip())
        if notes and notes.strip():
            lines.append(notes.strip())
        if not lines:
            return

        self.set_fill_color(*COL_BRANCH_BG)
        self.set_text_color(*COL_BRANCH_TEXT)
        self.set_font("Helvetica", "BI", 8)
        self.set_x(self.l_margin + 4)
        self.multi_cell(0, 4, safe("  BRANCH:  " + "   |   ".join(lines)),
                        fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def note_line(self, note: str):
        """Red-tinted note (clinician instructions)."""
        if not note:
            return
        self.set_text_color(*COL_NOTE_TEXT)
        self.set_font("Helvetica", "I", 8)
        self.set_x(self.l_margin + 4)
        self.multi_cell(0, 4, safe(f"Note: {note}"),
                        new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def separator(self):
        self.set_draw_color(*COL_LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1)


def render_question(pdf: QRef, q: dict):
    """Render one question block."""
    qid = q.get("id", "?")
    label = q.get("criterion_label", "")
    criterion_desc = (q.get("criterion_description") or "").strip()
    has_criterion = bool(criterion_desc)

    # Reserve space — start new page if less than 45mm left
    if pdf.get_y() > pdf.h - pdf.b_margin - 45:
        pdf.add_page()

    pdf.question_id_band(qid, label, has_criterion)

    # Interviewer text — handle conditional variants
    main_text = q.get("interviewer_text") or ""
    if_plus    = q.get("interviewer_text_if_previous_plus") or ""
    if_minus   = q.get("interviewer_text_if_previous_minus") or ""

    if main_text:
        pdf.interviewer_block(main_text)
    if if_plus:
        pdf.interviewer_block(if_plus, prefix="[If prev. +]")
    if if_minus:
        pdf.interviewer_block(if_minus, prefix="[If prev. -]")

    # Follow-ups
    for key, prefix in [
        ("follow_up_if_yes", "[If YES]"),
        ("follow_up_if_no",  "[If NO] "),
        ("follow_up",        "[Follow-up]"),
    ]:
        val = q.get(key) or ""
        if val:
            pdf.interviewer_block(val, prefix=prefix)

    # DSM-5 criterion
    if has_criterion:
        pdf.criterion_box(criterion_desc)

    # Rating options
    rating = q.get("rating_options") or {}
    if rating:
        pdf.rating_row(rating)

    # Branching / notes
    pdf.branch_box(q.get("skip_if"), None)
    pdf.note_line(q.get("notes") or "")

    pdf.ln(2)
    pdf.separator()


def build_toc(pdf: QRef, questions: dict):
    """Page 1: table of contents."""
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, "SCID-5-CV Interview -- Question Reference",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    total_q = sum(len(m["questions"]) for m in questions["modules"])
    pdf.cell(0, 6, safe(f"{len(questions['modules'])} modules  |  {total_q} questions total"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_draw_color(*COL_LINE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(20, 7, "Module", border="B")
    pdf.cell(100, 7, "Name", border="B")
    pdf.cell(30, 7, "Questions", border="B", align="C")
    pdf.cell(30, 7, "IDs", border="B")
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for m in questions["modules"]:
        ids = [q["id"] for q in m["questions"]]
        id_range = f"{ids[0]} - {ids[-1]}" if ids else "-"
        pdf.cell(20, 6, safe(m["id"]))
        pdf.cell(100, 6, safe(m.get("name_en", m["id"])[:55]))
        pdf.cell(30, 6, str(len(ids)), align="C")
        pdf.cell(30, 6, safe(id_range))
        pdf.ln()

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(0, 4, safe(
        "Legend:  [record only] = demographic/contextual question, no clinical rating.  "
        "BRANCH box = skip logic: 'If score -> go to target'.  "
        "DSM-5 box = the exact criterion being evaluated."
    ))
    pdf.set_text_color(0, 0, 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Output PDF path")
    args = parser.parse_args()

    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        questions = json.load(f)

    pdf = QRef()
    pdf.alias_nb_pages()

    # Cover / TOC page
    pdf.add_page()
    build_toc(pdf, questions)

    # One section per module
    for module in questions["modules"]:
        pdf.add_page()
        pdf.module_header(
            module["id"],
            module.get("name_en", module["id"]),
            len(module["questions"]),
        )
        for q in module["questions"]:
            render_question(pdf, q)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))

    size_kb = out.stat().st_size // 1024
    print(f"Saved: {out}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
