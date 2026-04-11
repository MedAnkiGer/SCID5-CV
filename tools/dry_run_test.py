"""
tools/dry_run_test.py

Dry-run integration test for the SCID-5-CV pipeline.
Tests everything except audio I/O (TTS + recording):
  - questions.json loading + indexing
  - rater API (real Claude calls on synthetic transcripts)
  - session state management
  - evaluation (module summaries)
  - PDF report generation

Usage:
    python tools/dry_run_test.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from main import (
    load_questions, build_question_index, next_question_id,
    create_session, save_session, session_dir, run_evaluation,
)
from modules.rater import evaluate_response, evaluate_with_clarification
from modules.reporter import generate_pdf

# ---------------------------------------------------------------------------
# Synthetic patient transcripts keyed by question ID
# We cover a few questions from different modules to test branching + rating.
# ---------------------------------------------------------------------------
SYNTHETIC_TRANSCRIPTS = {
    # Overview
    "OV1":  "I'm 34 years old.",
    "OV2":  "I work as a teacher.",

    # Module A — MDE criteria
    "A1":   "Yes, I've been feeling really depressed and down almost every day for the past month. It's been going on for about six weeks now.",
    "A2":   "I've lost interest in pretty much everything. Things I used to enjoy, like hiking and cooking, just don't interest me anymore.",
    "A3":   "I've lost about 8 kilograms over the past month without trying. My appetite is almost completely gone.",
    "A4":   "I wake up at 3 or 4 in the morning and can't get back to sleep. It's happening every night.",
    "A5":   "No, I haven't been especially restless or slowed down in a way others would notice.",
    "A6":   "I'm exhausted all the time. Even getting dressed feels like a huge effort.",
    "A7":   "I feel worthless, like I'm a burden to my family. I feel guilty about things I didn't even do wrong.",
    "A8":   "I can't concentrate at all. I keep reading the same sentence over and over.",
    "A9":   "I've had thoughts that I'd be better off dead, but I haven't made any plans.",

    # Module B — Psychotic symptoms (negative answers)
    "B1":   "No, I haven't heard voices or seen things that others couldn't see.",
    "B2":   "No, I haven't had the feeling that people were plotting against me.",

    # Module F — Anxiety (GAD-like)
    "F1":   "Yes, I worry excessively about many different things — work, health, my family — pretty much every day.",
    "F2":   "I find it very hard to control the worrying. It just takes over.",
}

CLARIFICATION_TRANSCRIPTS = {
    # If A9 comes back unresolved, we provide a clarification
    "A9": "I think about it maybe once or twice a week. I imagine going to sleep and not waking up, but I haven't done anything about it.",
}


def run_dry_run():
    print("=" * 60)
    print("SCID-5-CV Dry-Run Integration Test")
    print("=" * 60)

    # 1. Load questions
    print("\n[1] Loading questions.json...")
    questions = load_questions()
    index, order = build_question_index(questions)
    total_q = sum(len(m["questions"]) for m in questions["modules"])
    print(f"    {len(questions['modules'])} modules, {total_q} questions loaded.")
    print(f"    First 5 question IDs: {order[:5]}")

    # 2. Create session
    print("\n[2] Creating test session...")
    session = create_session(language="en")
    session_id = session["session_id"]
    print(f"    Session ID: {session_id}")

    # 3. Simulate interview: rate synthetic transcripts
    print("\n[3] Rating synthetic transcripts (real Claude API calls)...")
    session["stage"] = "INTERVIEW"
    errors = []

    for qid, transcript in SYNTHETIC_TRANSCRIPTS.items():
        q = index.get(qid)
        if q is None:
            print(f"    [SKIP] {qid} not found in index")
            continue

        has_criterion = bool((q.get("criterion_description") or "").strip())

        if not has_criterion:
            # Overview / demographic — record only, no rating
            print(f"    Record-only {qid} ({q.get('criterion_label', '')[:40]})")
            session["interview_responses"][qid] = {
                "score": "N/A",
                "transcript": transcript,
                "clarification_transcript": None,
                "unresolved": False,
                "reasoning": "",
            }
            session["current_question_id"] = qid
            save_session(session)
            continue

        print(f"    Rating {qid} ({q.get('criterion_label', '')[:40]}...")
        try:
            result = evaluate_response(transcript, q, language="en")
            score = result["score"]
            conf = result.get("confidence", "?")
            unresolved = result.get("unresolved", False)
            print(f"           score={score}  conf={conf:.2f}  unresolved={unresolved}")

            # Clarify if unresolved and we have a clarification transcript
            if unresolved and qid in CLARIFICATION_TRANSCRIPTS:
                clarif = CLARIFICATION_TRANSCRIPTS[qid]
                print(f"           -> Clarifying {qid}...")
                result = evaluate_with_clarification(transcript, clarif, q, language="en")
                result["clarification_transcript"] = clarif
                print(f"           -> After clarification: score={result['score']}  conf={result.get('confidence','?'):.2f}")
            else:
                result["clarification_transcript"] = None

            result["transcript"] = transcript
            result["reasoning"] = result.pop("rationale", "")
            session["interview_responses"][qid] = result
            session["current_question_id"] = qid
            save_session(session)

        except Exception as e:
            print(f"    [ERROR] {qid}: {e}")
            errors.append((qid, str(e)))

    # 4. Test branching logic
    print("\n[4] Testing branching logic...")
    test_cases = [
        ("A1", "+"),   # should advance to A2
        ("A2", "-"),   # should skip (both A1 and A2 are now "-" scenario)
        ("A10", "-"),  # should skip to A15
    ]
    for qid, score in test_cases:
        q = index.get(qid, {})
        nxt = next_question_id(qid, score, q, order)
        print(f"    {qid} score={score!r} -> next={nxt}")

    # 5. Run evaluation
    print("\n[5] Running evaluation...")
    run_evaluation(session, questions)
    print("    Module summaries:")
    for mid, s in session["module_summaries"].items():
        if s["questions_answered"] > 0:
            print(f"      {mid}: {s['questions_answered']} answered, {s['threshold_count']} threshold")

    # 6. Generate PDF
    print("\n[6] Generating PDF report...")
    session["stage"] = "REPORT"
    save_session(session)
    output_path = session_dir(session) / "dry_run_report.pdf"
    try:
        generate_pdf(session, questions, output_path)
        size_kb = output_path.stat().st_size // 1024
        print(f"    PDF saved: {output_path}  ({size_kb} KB)")
    except Exception as e:
        print(f"    [ERROR] PDF generation failed: {e}")
        errors.append(("pdf", str(e)))

    # 7. Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"DONE with {len(errors)} error(s):")
        for loc, msg in errors:
            print(f"  [{loc}] {msg}")
    else:
        print("ALL TESTS PASSED")
    print("=" * 60)
    print(f"\nSession state: data/sessions/{session_id}/state.json")
    print(f"PDF report  : data/sessions/{session_id}/dry_run_report.pdf")


if __name__ == "__main__":
    run_dry_run()
