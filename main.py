"""SCID-5-CV AI Interview Pipeline — Main Orchestrator.

State machine:
    INIT → INTERVIEW → EVALUATION → REPORT → COMPLETE

The INTERVIEW stage walks through SCID-5-CV modules and questions in order,
using JSON-defined branching logic. For each question:
  1. TTS reads the question aloud
  2. Patient answers verbally (recorded + Whisper-transcribed)
  3. Claude rates the answer (0/1/2 or +/-)
  4. If unresolved, one clarifying question is asked
  5. Branching logic advances to the next question or skips ahead

Sessions are resumable: state is saved after every question.
"""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

DATA_DIR = Path(__file__).resolve().parent / "data"
QUESTIONS_PATH = DATA_DIR / "questions.json"
SESSIONS_DIR = DATA_DIR / "sessions"


# ---------------------------------------------------------------------------
# Questions loading and indexing
# ---------------------------------------------------------------------------

def load_questions() -> dict:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_question_index(questions: dict) -> tuple[dict, list[str]]:
    """Build a flat {question_id: question_data} index and ordered ID list.

    Returns:
        index: dict mapping question_id → question dict (with 'module_id' injected)
        order: list of question IDs in interview order (across all modules)
    """
    index: dict[str, dict] = {}
    order: list[str] = []
    for module in questions["modules"]:
        for q in module["questions"]:
            q = dict(q)  # shallow copy so we can annotate
            q["module_id"] = module["id"]
            q["module_name_en"] = module.get("name_en", "")
            q["module_name_de"] = module.get("name_de", "")
            index[q["id"]] = q
            order.append(q["id"])
    return index, order


def next_question_id(
    current_id: str,
    score: str | int,
    q_data: dict,
    order: list[str],
) -> str | None:
    """Determine the next question ID given the current score and branching rules.

    Args:
        current_id: ID of the question just answered.
        score: The score assigned (e.g. '+', '-', 0, 1, 2, 'YES', 'NO').
        q_data: The question dict (may contain 'skip_if').
        order: Ordered list of all question IDs.

    Returns:
        Next question ID, or None if the interview is complete.
    """
    skip_if = q_data.get("skip_if")

    # Build a set of aliases for the score so we match whatever key Opus wrote
    score_str = str(score)
    score_aliases = {score_str}
    if score_str == "+":
        score_aliases |= {"YES", "yes", "1", "true"}
    elif score_str == "-":
        score_aliases |= {"NO", "no", "0", "false"}

    # skip_if can be a dict {score_key: target_id} or a free-text note (string)
    if isinstance(skip_if, dict):
        target = None
        for alias in score_aliases:
            if alias in skip_if:
                target = skip_if[alias]
                break
        if target and target in set(order):
            return target

    # Default: advance to the next question in order
    try:
        idx = order.index(current_id)
    except ValueError:
        return None
    return order[idx + 1] if idx + 1 < len(order) else None


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(language: str = "en") -> dict:
    session_id = str(uuid.uuid4())[:8]
    session = {
        "session_id": session_id,
        "created_at": datetime.now().isoformat(),
        "language": language,
        "stage": "INIT",
        "current_question_id": None,   # pointer into the interview
        "interview_responses": {},      # {question_id: response_dict}
        "module_summaries": {},         # filled at EVALUATION
    }
    save_session(session)
    return session


def session_dir(session: dict) -> Path:
    return SESSIONS_DIR / session["session_id"]


def save_session(session: dict) -> None:
    sdir = session_dir(session)
    sdir.mkdir(parents=True, exist_ok=True)
    with open(sdir / "state.json", "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)


def load_session(session_id: str) -> dict:
    with open(SESSIONS_DIR / session_id / "state.json", "r", encoding="utf-8") as f:
        return json.load(f)


def list_sessions() -> list[dict]:
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for d in SESSIONS_DIR.iterdir():
        state_file = d / "state.json"
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                s = json.load(f)
            sessions.append({
                "session_id": s["session_id"],
                "created_at": s["created_at"],
                "stage": s["stage"],
                "current_question_id": s.get("current_question_id"),
            })
    sessions.sort(key=lambda s: s["created_at"])
    return sessions


# ---------------------------------------------------------------------------
# Interview runner
# ---------------------------------------------------------------------------

def run_interview(session: dict, questions: dict) -> None:
    """Run the SCID-5-CV interview: TTS → record → transcribe → rate → branch.

    Modifies session in place; saves after every question.
    """
    from modules.exploration_engine import speak_text, record_and_transcribe
    from modules.rater import evaluate_response, evaluate_with_clarification

    lang = session.get("language", "en")
    index, order = build_question_index(questions)

    # Determine starting question (resume support)
    start_id = session.get("current_question_id") or (order[0] if order else None)
    if start_id is None:
        print("No questions found in questions.json.")
        return

    # Skip already-answered questions
    if start_id in session["interview_responses"]:
        answered = set(session["interview_responses"].keys())
        start_id = next((qid for qid in order if qid not in answered), None)

    if start_id is None:
        print("All questions already answered.")
        session["stage"] = "EVALUATION"
        save_session(session)
        return

    session["stage"] = "INTERVIEW"
    current_id = start_id

    while current_id is not None:
        q = index.get(current_id)
        if q is None:
            print(f"[WARN] Question ID '{current_id}' not found in index. Stopping.")
            break

        # Skip if already answered (resume path)
        if current_id in session["interview_responses"]:
            result = session["interview_responses"][current_id]
            score = result.get("score", "-")
            current_id = next_question_id(current_id, score, q, order)
            continue

        # --- Announce module transition ---
        prev_id = order[order.index(current_id) - 1] if order.index(current_id) > 0 else None
        if prev_id and index.get(prev_id, {}).get("module_id") != q["module_id"]:
            module_name = q.get(f"module_name_{lang}") or q.get("module_name_en", "")
            if module_name:
                print(f"\n=== Module {q['module_id']}: {module_name} ===")

        # --- Build text to speak ---
        question_text = q.get(f"interviewer_text_{lang}") or q.get("interviewer_text", "")
        if not question_text:
            # No text to read (e.g. a summary/scoring-only criterion) — skip playback
            print(f"[{current_id}] No interviewer text. Skipping to next.")
            session["interview_responses"][current_id] = {
                "score": "skip",
                "transcript": "",
                "clarification_transcript": None,
            }
            session["current_question_id"] = current_id
            save_session(session)
            current_id = next_question_id(current_id, "skip", q, order)
            continue

        # Display question clearly
        print(f"\n{'─' * 60}")
        print(f"[{current_id}]  {q.get('criterion_label', '')}")
        print(f"{'─' * 60}")
        print(f"{question_text}")
        print()

        # 1. Speak the question
        speak_text(question_text, language=lang)

        # 2. Record + transcribe — loop until accepted
        while True:
            result = record_and_transcribe(language=lang)
            transcript = result["transcript"]
            choice = input("  [Enter] Accept  |  [r] Redo: ").strip().lower()
            if choice != "r":
                break
            print("  Replaying question...")
            speak_text(question_text, language=lang)

        # 3. Rate — skip if no DSM-5 criterion to rate against (e.g. Overview questions)
        has_criterion = bool((q.get("criterion_description") or "").strip())
        clarification_transcript = None

        if not has_criterion:
            # Demographic / contextual question — just record the answer
            session["interview_responses"][current_id] = {
                "score": "N/A",
                "transcript": transcript,
                "clarification_transcript": None,
                "unresolved": False,
                "reasoning": "",
            }
        else:
            print(f"  Rating...")
            rating = evaluate_response(transcript, q, lang)
            score = rating.get("score", "?")
            print(f"  Score: {score}")

            # 4. Clarify if unresolved (max 1 attempt)
            if rating.get("unresolved") and rating.get("clarifying_question"):
                clarifying_q = rating["clarifying_question"]
                print(f"\n  [Follow-up]: {clarifying_q}")
                speak_text(clarifying_q, language=lang)
                while True:
                    clarif_result = record_and_transcribe(language=lang)
                    clarification_transcript = clarif_result["transcript"]
                    choice = input("  [Enter] Accept  |  [r] Redo: ").strip().lower()
                    if choice != "r":
                        break
                    speak_text(clarifying_q, language=lang)
                rating = evaluate_with_clarification(
                    transcript, clarification_transcript, q, lang
                )
                score = rating.get("score", "?")
                print(f"  Score after clarification: {score}")

            # 5. Save response
            session["interview_responses"][current_id] = {
                "score": score,
                "transcript": transcript,
                "clarification_transcript": clarification_transcript,
                "unresolved": rating.get("unresolved", False),
                "reasoning": rating.get("reasoning", ""),
            }
        session["current_question_id"] = current_id
        save_session(session)

        # 6. Branch to next question (use stored score so both paths are covered)
        score = session["interview_responses"][current_id]["score"]
        current_id = next_question_id(current_id, score, q, order)

    session["stage"] = "EVALUATION"
    save_session(session)
    print("\nInterview complete.")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def run_evaluation(session: dict, questions: dict) -> None:
    """Summarise scores per module and flag which modules meet criteria."""
    responses = session["interview_responses"]
    summaries = {}

    for module in questions["modules"]:
        mid = module["id"]
        q_ids = [q["id"] for q in module["questions"]]
        answered = {qid: responses[qid] for qid in q_ids if qid in responses}
        threshold_count = sum(1 for r in answered.values() if r.get("score") == "+")
        summaries[mid] = {
            "module_name": module.get("name_en", mid),
            "questions_answered": len(answered),
            "threshold_count": threshold_count,
        }

    session["module_summaries"] = summaries
    session["stage"] = "REPORT"
    save_session(session)

    print("\nModule Summaries:")
    for mid, s in summaries.items():
        print(f"  {mid} — {s['module_name']}: {s['threshold_count']} threshold items "
              f"({s['questions_answered']} answered)")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_report(session: dict, questions: dict) -> None:
    """Generate the clinical PDF report."""
    from modules.reporter import generate_pdf

    output_path = session_dir(session) / "report.pdf"
    generate_pdf(session, questions, output_path)
    session["stage"] = "COMPLETE"
    save_session(session)
    print(f"\nReport saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    questions = load_questions()
    session = None

    sessions = list_sessions()
    if sessions:
        incomplete = [s for s in sessions if s["stage"] != "COMPLETE"]
        if incomplete:
            print("Incomplete sessions:")
            for i, s in enumerate(incomplete):
                print(f"  [{i}] {s['session_id']} — Stage: {s['stage']} "
                      f"— Q: {s.get('current_question_id', '—')} — {s['created_at']}")
            print("  [n] New session")
            choice = input("\nResume or new? ").strip().lower()
            if choice != "n" and choice.isdigit() and int(choice) < len(incomplete):
                session = load_session(incomplete[int(choice)]["session_id"])
                print(f"Resuming session {session['session_id']} at stage {session['stage']}")

    if session is None:
        lang = input("Language [en/de, default en]: ").strip().lower() or "en"
        session = create_session(language=lang)
        print(f"New session: {session['session_id']}")

    stage = session["stage"]

    if stage in ("INIT", "INTERVIEW"):
        run_interview(session, questions)
        stage = session["stage"]

    if stage == "EVALUATION":
        run_evaluation(session, questions)
        stage = session["stage"]

    if stage == "REPORT":
        run_report(session, questions)
        stage = session["stage"]

    if stage == "COMPLETE":
        print(f"\nSession complete. Report: {session_dir(session) / 'report.pdf'}")


if __name__ == "__main__":
    main()
