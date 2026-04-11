"""
tools/extract_scid5cv.py

Extracts SCID-5-CV interview questions from a scanned PDF using Claude Vision (Opus).
Outputs: data/questions.json with modules, questions, and branching logic.

Usage:
    python tools/extract_scid5cv.py --pdf PATH_TO_PDF [--pages START-END] [--out PATH]

The extraction runs page by page and appends to a staging file so it can be
resumed if interrupted. Final output is merged into questions.json.
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import anthropic
from dotenv import load_dotenv

# Load .env from the project root (one level up from tools/)
_root = Path(__file__).parent.parent
load_dotenv(_root / ".env", override=True)

# Page ranges for each module (0-indexed, inclusive)
# Adjust if the PDF differs slightly
MODULE_PAGE_RANGES = {
    "overview":     (9,  11),   # pages 10-12
    "A_mood":       (12, 33),   # pages 13-34: MDE, Manic, Hypomanic
    "B_psychotic":  (34, 41),   # pages 35-42
    "C_psychdiff":  (42, 49),   # pages 43-50
    "D_mooddiff":   (50, 57),   # pages 51-58
    "E_substance":  (58, 65),   # pages 59-66
    "F_anxiety":    (66, 76),   # pages 67-77
    "G_ptsd":       (78, 89),   # pages 79-90
    "H_adhd":       (90, 93),   # pages 91-94
    "I_other":      (94, 97),   # pages 95-98
}

EXTRACTION_PROMPT = """You are extracting structured data from a page of the SCID-5-CV
(Structured Clinical Interview for DSM-5, Clinician Version).

Extract ALL interview questions visible on this page into the following JSON format.
Return ONLY a JSON object — no prose, no markdown fences.

{
  "module_id": "A",
  "module_name": "Major Depressive Episode",
  "questions": [
    {
      "id": "A1",
      "criterion_label": "Depressed mood",
      "interviewer_text": "In the last month, has there been a period of time when you were feeling depressed or down most of the day, nearly every day? (How long did it last?)",
      "criterion_description": "Depressed mood most of the day, nearly every day, as indicated by either subjective report or observation by others.",
      "rating_options": {"1": "YES", "0": "NO"},
      "skip_if": {"0": "A5"},
      "notes": "Optional admin note visible on page"
    }
  ]
}

Rules:
- id: use the alphanumeric code on the page (e.g. A1, B3, C11)
- interviewer_text: the EXACT verbatim text the clinician reads to the patient (in quotes or bold on the page)
- criterion_description: the DSM-5 criterion text (often in italics or a box)
- rating_options: what the rater marks (e.g. {"1":"YES","0":"NO"} or {"3":"Threshold","2":"Subthreshold","1":"Present","0":"Absent"})
- skip_if: branching logic — if rating equals key, skip to question id in value. E.g. {"0":"A5"} means if NO, skip to A5
- If a question spans multiple columns or has sub-parts, include each as a separate entry
- If this page has no interview questions (e.g. score sheet, table of codes), return {"module_id": null, "module_name": null, "questions": []}
- Preserve exact wording — this is a clinical instrument
"""


def render_page(doc: fitz.Document, page_idx: int, dpi: int = 200) -> bytes:
    """Render a PDF page to PNG bytes."""
    page = doc[page_idx]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


def extract_page(client: anthropic.Anthropic, png_bytes: bytes, page_num: int) -> dict:
    """Send a page image to Claude Opus and return parsed JSON."""
    b64 = base64.standard_b64encode(png_bytes).decode()
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64}
                },
                {
                    "type": "text",
                    "text": EXTRACTION_PROMPT
                }
            ]
        }]
    )
    raw = resp.content[0].text.strip()
    # Strip any accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON parse error on page {page_num}: {e}", file=sys.stderr)
        print(f"  Raw response: {raw[:200]}", file=sys.stderr)
        return {"module_id": None, "module_name": None, "questions": [], "_raw": raw}


def merge_into_modules(all_pages: list[dict]) -> dict:
    """Merge per-page extractions into a single modules dict."""
    modules: dict[str, dict] = {}
    for page_data in all_pages:
        mid = page_data.get("module_id")
        if not mid or not page_data.get("questions"):
            continue
        if mid not in modules:
            modules[mid] = {
                "id": mid,
                "name_en": page_data.get("module_name", ""),
                "name_de": "",
                "questions": []
            }
        # Deduplicate by question id
        existing_ids = {q["id"] for q in modules[mid]["questions"]}
        for q in page_data.get("questions", []):
            if q["id"] not in existing_ids:
                # Add _de fields
                q["interviewer_text_de"] = ""
                q["criterion_description_de"] = ""
                modules[mid]["questions"].append(q)
                existing_ids.add(q["id"])
    return modules


def main():
    parser = argparse.ArgumentParser(description="Extract SCID-5-CV questions from PDF")
    parser.add_argument("--pdf", required=True, help="Path to SCID-5-CV PDF")
    parser.add_argument(
        "--pages", default=None,
        help="Page range to extract (e.g. '10-50'). Default: all interview pages"
    )
    parser.add_argument(
        "--out", default="data/questions.json",
        help="Output path for questions.json"
    )
    parser.add_argument(
        "--staging", default="data/questions_staging.jsonl",
        help="Staging file for resumable extraction"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds to wait between API calls"
    )
    args = parser.parse_args()

    client = anthropic.Anthropic()
    doc = fitz.open(args.pdf)
    total_pages = len(doc)
    print(f"PDF loaded: {total_pages} pages")

    # Determine page range
    if args.pages:
        start, end = (int(x) - 1 for x in args.pages.split("-"))
    else:
        # Default: all interview pages (skip score sheets at start)
        start, end = 9, total_pages - 1

    # Load already-processed pages from staging file
    staging_path = Path(args.staging)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    done_pages: set[int] = set()
    all_results: list[dict] = []
    if staging_path.exists():
        with staging_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    done_pages.add(entry["_page"])
                    all_results.append(entry)
        print(f"Resuming: {len(done_pages)} pages already done")

    # Process each page
    with staging_path.open("a", encoding="utf-8") as staging_f:
        for pi in range(start, end + 1):
            if pi in done_pages:
                continue
            page_num = pi + 1
            print(f"  Extracting page {page_num}/{total_pages}...", end=" ", flush=True)
            try:
                png_bytes = render_page(doc, pi)
                result = extract_page(client, png_bytes, page_num)
                result["_page"] = pi
                all_results.append(result)
                staging_f.write(json.dumps(result) + "\n")
                staging_f.flush()
                q_count = len(result.get("questions", []))
                print(f"ok ({q_count} questions, module={result.get('module_id')})")
            except Exception as e:
                print(f"ERROR: {e}", file=sys.stderr)
            time.sleep(args.delay)

    # Merge and write final output
    modules = merge_into_modules(all_results)
    output = {
        "version": "scid5-cv-v1",
        "language": "en",
        "modules": list(modules.values())
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    total_q = sum(len(m["questions"]) for m in modules.values())
    print(f"\nDone. {len(modules)} modules, {total_q} questions -> {args.out}")
    print(f"Staging file kept at: {args.staging} (delete to re-run from scratch)")


if __name__ == "__main__":
    main()
