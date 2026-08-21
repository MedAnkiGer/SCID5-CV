"""One-off cleanup of `skip_if` in data/questions.json.

The extraction from the SCID-5-CV booklet left three problems in the branching
data, all of which made a skip silently fall through to the next question in
order instead of jumping where the booklet says:

1. Targets that are prose rather than question ids
   ("B1 (Psychotic Symptoms), page 31", "Go to F42 (GAD), page 71.").
2. Score keys that no score can ever match: U+2212 MINUS SIGN and U+2014 EM
   DASH where a plain hyphen was meant, plus one free-text key on D1.
3. One question in Module A with `id: null`, which put a None into the question
   order and could never be stored or branched from.

This script rewrites those to machine-usable values from the table below, and
preserves every original string in a new `skip_if_note` field so no clinical
instruction is lost — several targets also carried a diagnosis to record
("Diagnose: Bipolar Disorder Due to AMC ... Continue with A54").

Two sentinels are used where the booklet text names no next question:
  END       stop the interview (the Module J "END OF SCID-5-CV" branches)
  CONTINUE  carry on in order — the current behaviour, but now stated on
            purpose rather than by accident. These are the diagnosis endpoints
            and the "PRIMARY" placeholders, where the extraction did not record
            a destination. Each is listed in OPEN_QUESTIONS.md.

Run from the project root; it is idempotent.

    python tools/fix_skip_targets.py            # apply
    python tools/fix_skip_targets.py --check    # report only, change nothing
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = ROOT / "data" / "questions.json"

# Score keys that no rating can match, mapped to the score that was meant.
KEY_FIXES = {
    "−": "-",                                  # MINUS SIGN
    "—": "-",                                  # EM DASH
    "no_mood_symptoms_or_schizoaffective": "-",     # D1, free text for "no mood sxs"
}

# Target resolutions, keyed by (question id, score key AFTER key normalisation).
# Every entry either names a question id that exists, or a sentinel.
TARGET_FIXES = {
    # --- Overview: the suicide/other-problems blocks ---
    ("OV_SI1", "NO"): "OV_SA1",
    ("OV_SI2", "NO"): "OV_SA1",
    ("OV_SA2", "NO"): "OV_OCP1",

    # --- Module A ---
    ("A12", "NO"): "CONTINUE",      # "PRIMARY" — no destination recorded
    ("A12", "YES"): "CONTINUE",     # "A12_diagnoses" placeholder
    ("A24", "NO"): "A25",
    ("A38", "0"): "A54",
    ("A39", "-"): "A50",
    ("A40", "0"): "A54",            # after recording the substance/AMC diagnosis
    ("A53", "1"): "CONTINUE",       # "PRIMARY"
    ("A55", "-"): "A66",
    ("A63", "NO"): "A78",           # prose ends "Continue with A78 ... page 29"
    ("A77", "YES"): "CONTINUE",     # "PRIMARY"
    ("A78", "-"): "B1",
    ("A85", "NO"): "B1",
    ("A86", "-"): "B1",
    ("A87", "-"): "B1",
    ("A88", "-"): "B1",
    ("A89", "-"): "CONTINUE",       # diagnosis only; A90 then leads to B1
    ("A90", "-"): "B1",

    # --- Module C: substance/medical rule-outs that end in a diagnosis ---
    ("C6", "NO"): "CONTINUE",       # "C_substance_induced"
    ("C8", "0"): "CONTINUE",        # "C8_NO_PATH"
    ("C12", "0"): "CONTINUE",       # "C12_AMC"
    ("C17", "0"): "CONTINUE",       # "C17_diag"
    ("C24", "NO"): "CONTINUE",      # "C24_diagnosis"

    # --- Module D: episode-specifier endpoints ---
    ("D17", "NO"): "CONTINUE",
    ("D18", "NO"): "CONTINUE",
    ("D19", "0"): "CONTINUE",       # past history -> record specifier, carry on
    ("D19", "1"): "E1",
    ("D20", "0"): "CONTINUE",
    ("D20", "1"): "E1",
    ("D21", "0"): "CONTINUE",
    ("D21", "1"): "E1",

    # --- Module E ---
    ("E35", "NO"): "CONTINUE",   # booklet says "Continue with E36" — E36 was never
                                 # extracted; E35 is the last item in Module E, so
                                 # carrying on in order reaches F1 as intended.

    # --- Module F ---
    ("F19", "NO"): "F23",           # "F19_go_to_F23"
    ("F20", "NO"): "CONTINUE",      # "F20_PRIMARY"
    ("F26", "-"): "F32",
    ("F27", "-"): "F32",
    ("F28", "-"): "F32",
    ("F29", "-"): "F32",
    ("F30", "-"): "F32",
    ("F31", "0"): "F32",
    ("F32", "0"): "F42",
    ("F33", "0"): "F42",
    ("F34", "0"): "F42",
    ("F35", "-"): "F42",
    ("F36", "-"): "F42",
    ("F37", "-"): "F42",
    ("F38", "-"): "F42",
    ("F39", "0"): "CONTINUE",       # "PRIMARY"
    ("F42", "-"): "G1",
    ("F43", "-"): "G1",
    ("F44", "-"): "G1",
    ("F51", "-"): "G1",
    ("F52", "-"): "G1",
    ("F53", "YES"): "G1",           # after recording the substance/AMC diagnosis
    ("F54", "NO"): "G1",

    # --- Module G ---
    ("G5", "0"): "G9",
    ("G6", "-"): "G9",
    ("G7", "0"): "CONTINUE",        # "PRIMARY"

    # --- Module H: "II (Screening), page 91" is the Module I screen, I1 ---
    ("H22", "NO"): "I1",
    ("H23", "-"): "I1",
    ("H24", "-"): "I1",
    ("H25", "-"): "I1",
    ("H26", "NO"): "I1",

    # --- Module J: the end of the instrument ---
    ("J1", "-"): "END",
    ("J2", "-"): "END",
    ("J3", "0"): "END",
    ("J4", "0"): "END",
    ("J5", "-"): "END",
}

# The Module A question that came through with no id, and the id given to it.
# It sits between A65 and A66 and screens for further past manic episodes.
MISSING_ID = ("A", "Additional past manic episodes screening", "A65b")


def main() -> int:
    check_only = "--check" in sys.argv

    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    valid_ids = {q["id"] for m in data["modules"] for q in m["questions"] if q.get("id")}
    valid_ids.add(MISSING_ID[2])
    sentinels = {"END", "CONTINUE"}

    named, keys_fixed, ids_fixed, unresolved = 0, 0, 0, []

    for module in data["modules"]:
        for q in module["questions"]:
            # 1. the missing id
            if not q.get("id") and module["id"] == MISSING_ID[0] \
                    and q.get("criterion_label") == MISSING_ID[1]:
                q["id"] = MISSING_ID[2]
                ids_fixed += 1

            skip_if = q.get("skip_if")
            if not isinstance(skip_if, dict):
                continue

            # 2. unmatchable score keys
            rekeyed = {}
            for key, target in skip_if.items():
                new_key = KEY_FIXES.get(key, key)
                if new_key != key:
                    keys_fixed += 1
                rekeyed[new_key] = target

            # 3. prose targets
            notes = dict(q.get("skip_if_note") or {})
            resolved = {}
            for key, target in rekeyed.items():
                if target in valid_ids or target in sentinels:
                    resolved[key] = target
                    continue
                fix = TARGET_FIXES.get((q["id"], key))
                if fix is None:
                    unresolved.append((q["id"], key, target))
                    resolved[key] = target
                    continue
                notes.setdefault(key, target)   # keep the booklet wording verbatim
                resolved[key] = fix
                named += 1

            if not check_only:
                q["skip_if"] = resolved
                if notes:
                    q["skip_if_note"] = notes

    print(f"targets resolved : {named}")
    print(f"score keys fixed : {keys_fixed}")
    print(f"missing ids fixed: {ids_fixed}")
    if unresolved:
        print(f"\nSTILL UNRESOLVED ({len(unresolved)}) — add them to TARGET_FIXES:")
        for qid, key, target in unresolved:
            print(f"  {qid:<8} {key!r:<8} -> {target!r}")

    if check_only:
        print("\n--check: nothing written")
        return 1 if unresolved else 0

    with open(QUESTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"\nwritten: {QUESTIONS_PATH}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
