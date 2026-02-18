"""CLI tool to add real SCID-5-PD questions to questions.json.

Usage:
    python tools/question_entry.py

Walks you through adding a screening item and/or criterion to the question bank.
Validates the schema on save.
"""

import json
import sys
from pathlib import Path

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "questions.json"

VALID_DISORDERS = [
    "paranoid", "schizoid", "schizotypal",           # Cluster A
    "antisocial", "borderline", "histrionic", "narcissistic",  # Cluster B
    "avoidant", "dependent", "obsessive_compulsive",  # Cluster C
]

CLUSTER_MAP = {
    "paranoid": "A", "schizoid": "A", "schizotypal": "A",
    "antisocial": "B", "borderline": "B", "histrionic": "B", "narcissistic": "B",
    "avoidant": "C", "dependent": "C", "obsessive_compulsive": "C",
}


def load_questions() -> dict:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_questions(data: dict) -> None:
    with open(QUESTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved to {QUESTIONS_PATH}")


def validate_schema(data: dict) -> list[str]:
    """Basic validation. Returns list of error messages."""
    errors = []
    if "metadata" not in data:
        errors.append("Missing 'metadata' key")
    if "disorders" not in data:
        errors.append("Missing 'disorders' key")
    if "screening_items" not in data:
        errors.append("Missing 'screening_items' key")

    for item_id, item in data.get("screening_items", {}).items():
        for field in ("text_de", "text_en", "maps_to_criteria", "disorder"):
            if field not in item:
                errors.append(f"Screening item {item_id} missing '{field}'")
        disorder = item.get("disorder")
        if disorder and disorder not in data.get("disorders", {}):
            errors.append(f"Screening item {item_id} references unknown disorder '{disorder}'")
        for crit_id in item.get("maps_to_criteria", []):
            disorder_data = data.get("disorders", {}).get(disorder, {})
            if crit_id not in disorder_data.get("criteria", {}):
                errors.append(f"Screening item {item_id} references unknown criterion '{crit_id}'")

    return errors


def next_item_id(data: dict) -> str:
    existing = [int(k[1:]) for k in data.get("screening_items", {}).keys() if k.startswith("Q")]
    next_num = max(existing, default=0) + 1
    return f"Q{next_num}"


def add_screening_item(data: dict) -> None:
    print("\n--- Add Screening Item ---")
    print(f"Available disorders: {', '.join(data['disorders'].keys())}")
    disorder = input("Disorder: ").strip().lower()
    if disorder not in data["disorders"]:
        print(f"Error: '{disorder}' not in question bank. Add the disorder first.")
        return

    disorder_data = data["disorders"][disorder]
    criteria = list(disorder_data["criteria"].keys())
    print(f"Available criteria for {disorder}: {', '.join(criteria)}")
    criterion_id = input("Criterion ID: ").strip()
    if criterion_id not in disorder_data["criteria"]:
        print(f"Error: '{criterion_id}' not found. Add the criterion first.")
        return

    item_id = next_item_id(data)
    print(f"New item ID: {item_id}")

    text_de = input("Question text (DE): ").strip()
    text_en = input("Question text (EN): ").strip()

    data["screening_items"][item_id] = {
        "text_de": text_de,
        "text_en": text_en,
        "maps_to_criteria": [criterion_id],
        "disorder": disorder,
    }

    # Also add item ID to criterion's screening_item_ids
    if item_id not in disorder_data["criteria"][criterion_id]["screening_item_ids"]:
        disorder_data["criteria"][criterion_id]["screening_item_ids"].append(item_id)

    data["metadata"]["total_screening_items"] = len(data["screening_items"])
    print(f"Added {item_id} -> {criterion_id} ({disorder})")


def add_criterion(data: dict) -> None:
    print("\n--- Add Criterion ---")
    print(f"Available disorders: {', '.join(data['disorders'].keys())}")
    disorder = input("Disorder: ").strip().lower()
    if disorder not in data["disorders"]:
        print(f"Error: '{disorder}' not in question bank.")
        return

    criterion_id = input("Criterion ID (e.g. BPD_4): ").strip()
    if criterion_id in data["disorders"][disorder]["criteria"]:
        print(f"Criterion '{criterion_id}' already exists.")
        return

    desc_de = input("Description (DE): ").strip()
    desc_en = input("Description (EN): ").strip()
    followup_de = input("Follow-up question (DE): ").strip()
    followup_en = input("Follow-up question (EN): ").strip()

    data["disorders"][disorder]["criteria"][criterion_id] = {
        "description_de": desc_de,
        "description_en": desc_en,
        "screening_item_ids": [],
        "followup_question_de": followup_de,
        "followup_question_en": followup_en,
    }
    print(f"Added criterion {criterion_id} to {disorder}")


def add_disorder(data: dict) -> None:
    print("\n--- Add Disorder ---")
    print(f"Valid disorder names: {', '.join(VALID_DISORDERS)}")
    name = input("Disorder key: ").strip().lower()
    if name not in VALID_DISORDERS:
        print(f"Warning: '{name}' not in standard list. Continuing anyway.")

    if name in data["disorders"]:
        print(f"Disorder '{name}' already exists.")
        return

    dsm5_code = input("DSM-5 code (e.g. 301.83): ").strip()
    name_de = input("Name (DE): ").strip()
    name_en = input("Name (EN): ").strip()
    threshold = int(input("Threshold (criteria needed for diagnosis): ").strip())

    data["disorders"][name] = {
        "cluster": CLUSTER_MAP.get(name, "?"),
        "dsm5_code": dsm5_code,
        "name_de": name_de,
        "name_en": name_en,
        "threshold": threshold,
        "criteria": {},
    }
    print(f"Added disorder '{name}'")


def main():
    data = load_questions()
    print(f"Loaded question bank v{data['metadata']['version']}")
    print(f"  {len(data['screening_items'])} screening items")
    print(f"  {len(data['disorders'])} disorders")

    while True:
        print("\nOptions:")
        print("  1. Add screening item")
        print("  2. Add criterion")
        print("  3. Add disorder")
        print("  4. Validate schema")
        print("  5. Save & quit")
        print("  6. Quit without saving")

        choice = input("\nChoice: ").strip()

        if choice == "1":
            add_screening_item(data)
        elif choice == "2":
            add_criterion(data)
        elif choice == "3":
            add_disorder(data)
        elif choice == "4":
            errors = validate_schema(data)
            if errors:
                print("Validation errors:")
                for e in errors:
                    print(f"  - {e}")
            else:
                print("Schema is valid!")
        elif choice == "5":
            errors = validate_schema(data)
            if errors:
                print("Warning — validation errors found:")
                for e in errors:
                    print(f"  - {e}")
                confirm = input("Save anyway? (y/n): ").strip().lower()
                if confirm != "y":
                    continue
            save_questions(data)
            break
        elif choice == "6":
            print("Quit without saving.")
            break
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()
