#!/usr/bin/env python3
"""
Import exploration questions from the review text file into questions.json.

Usage:
    python tools/import_exploration.py                    # import and update questions.json
    python tools/import_exploration.py --dry-run          # preview without writing
    python tools/import_exploration.py --show Q25         # show a single item
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEW_FILE = ROOT / "data" / "exploration_questions_review.txt"
QUESTIONS_FILE = ROOT / "data" / "questions.json"


def parse_review_file(path: Path) -> dict:
    """Parse the review text file into a dict keyed by Q-number."""
    items = {}
    current = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line_stripped = line.strip().rstrip("\r")

        # Skip empty lines
        if not line_stripped:
            continue

        # Header line: ### Q1 | avoidant | criterion_1
        header_match = re.match(
            r"^###\s+(Q\w+)\s*\|\s*(\w+)\s*\|\s*(.+)$", line_stripped
        )
        if header_match:
            qid = header_match.group(1)
            disorder = header_match.group(2)
            criterion = header_match.group(3).strip()
            current = {
                "qid": qid,
                "disorder": disorder,
                "criterion": criterion,
                "main": "",
                "probes": [],
            }
            items[qid] = current
            continue

        # Skip comments (lines starting with # but not ###)
        if line_stripped.startswith("#") and not line_stripped.startswith("###"):
            continue

        if current is None:
            continue

        # MAIN: line
        if line_stripped.startswith("MAIN:"):
            current["main"] = line_stripped[5:].strip()
        # PROBE: line
        elif line_stripped.startswith("PROBE:"):
            current["probes"].append(line_stripped[6:].strip())

    return items


def update_questions_json(items: dict, dry_run: bool = False):
    """Merge exploration data into questions.json."""
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Update screening items with exploration questions
    for qid, item in items.items():
        if qid in data["screening_items"]:
            si = data["screening_items"][qid]
            si["exploration_main_de"] = item["main"]
            si["exploration_probes_de"] = item["probes"]

            # Update maps_to_criteria if empty
            if not si.get("maps_to_criteria"):
                si["maps_to_criteria"] = [item["criterion"]]
            elif item["criterion"] not in si["maps_to_criteria"]:
                # Don't overwrite existing mappings, but note the criterion
                pass

        elif qid.startswith("QA"):
            # Adult antisocial criteria — store in disorder block
            disorder_data = data["disorders"].get("antisocial", {})
            if "adult_criteria" not in disorder_data:
                disorder_data["adult_criteria"] = {}
            disorder_data["adult_criteria"][qid] = {
                "criterion": item["criterion"],
                "main_de": item["main"],
                "probes_de": item["probes"],
            }

    # Update metadata
    data["metadata"]["version"] = "0.4-exploration"
    data["metadata"]["note"] = (
        "German screening items + exploration questions populated from "
        "SCID-5-SPQ and SCID-5-PD Interviewheft."
    )

    if dry_run:
        # Count what would be updated
        n_screening = sum(1 for q in items if q in data["screening_items"])
        n_adult = sum(1 for q in items if q.startswith("QA"))
        print(f"Would update {n_screening} screening items and {n_adult} adult criteria.")
        print(f"Sample (Q1):")
        q1 = items.get("Q1")
        if q1:
            print(f"  MAIN: {q1['main'][:80]}...")
            print(f"  PROBES: {len(q1['probes'])}")
    else:
        with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        n_screening = sum(1 for q in items if q in data["screening_items"])
        n_adult = sum(1 for q in items if q.startswith("QA"))
        print(f"Updated {n_screening} screening items and {n_adult} adult criteria.")
        print(f"Written to {QUESTIONS_FILE}")


def show_item(items: dict, qid: str):
    """Display a single parsed item."""
    item = items.get(qid)
    if not item:
        print(f"Item {qid} not found. Available: {sorted(items.keys())}")
        return
    print(f"=== {item['qid']} | {item['disorder']} | {item['criterion']} ===")
    print(f"MAIN: {item['main']}")
    for i, probe in enumerate(item["probes"], 1):
        print(f"PROBE {i}: {probe}")


def main():
    args = sys.argv[1:]

    items = parse_review_file(REVIEW_FILE)
    print(f"Parsed {len(items)} exploration items from {REVIEW_FILE.name}")

    if "--show" in args:
        idx = args.index("--show")
        qid = args[idx + 1] if idx + 1 < len(args) else "Q1"
        show_item(items, qid)
    elif "--dry-run" in args:
        update_questions_json(items, dry_run=True)
    else:
        update_questions_json(items, dry_run=False)


if __name__ == "__main__":
    main()
