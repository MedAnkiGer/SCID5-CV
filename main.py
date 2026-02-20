"""SCID-5-PD AI Pipeline — Main Orchestrator.

State machine that ties all stages together:
    INIT -> SELF_REPORT -> EXPLORATION -> EVALUATION -> REPORT -> COMPLETE

Sessions are resumable: state is saved after every step.
"""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent / "data"
QUESTIONS_PATH = DATA_DIR / "questions.json"
SESSIONS_DIR = DATA_DIR / "sessions"


def load_questions() -> dict:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def create_session(language: str = "de") -> dict:
    session_id = str(uuid.uuid4())[:8]
    session = {
        "session_id": session_id,
        "created_at": datetime.now().isoformat(),
        "language": language,
        "stage": "INIT",
        "screening_responses": {},
        "exploration_results": {},
        "disorder_verdicts": {},
    }
    save_session(session)
    return session


def session_dir(session: dict) -> Path:
    return SESSIONS_DIR / session["session_id"]


def save_session(session: dict) -> None:
    sdir = session_dir(session)
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / "state.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)


def load_session(session_id: str) -> dict:
    path = SESSIONS_DIR / session_id / "state.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sessions() -> list[dict]:
    """List all existing sessions with basic info."""
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
            })
    sessions.sort(key=lambda s: s["created_at"])
    return sessions


def get_flagged_criteria(session: dict, questions: dict) -> list[dict]:
    """From screening responses, find all criteria that need exploration.

    Returns list of dicts with criterion_id and criterion data, deduplicated.
    """
    flagged_criteria = {}

    for item_id, answered_yes in session["screening_responses"].items():
        if not answered_yes:
            continue

        item = questions["screening_items"].get(item_id)
        if not item:
            continue

        disorder_key = item["disorder"]
        disorder = questions["disorders"].get(disorder_key)
        if not disorder:
            continue

        for crit_id in item["maps_to_criteria"]:
            if crit_id in flagged_criteria:
                continue
            crit_data = disorder["criteria"].get(crit_id)
            if crit_data:
                flagged_criteria[crit_id] = {
                    "criterion_id": crit_id,
                    "disorder": disorder_key,
                    **crit_data,
                }

    return list(flagged_criteria.values())


def compute_disorder_verdicts(session: dict, questions: dict) -> dict:
    """Compute diagnosis verdicts based on exploration results."""
    verdicts = {}

    for disorder_key, disorder_data in questions["disorders"].items():
        criteria = disorder_data["criteria"]
        threshold = disorder_data["threshold"]
        criteria_met = 0
        has_unresolved = False

        for crit_id in criteria:
            result = session["exploration_results"].get(crit_id)
            if result and result.get("score") == 2:
                criteria_met += 1
            if result and (result.get("unresolved") or result.get("score") == "?"):
                has_unresolved = True

        # Only set diagnosis if disorder was actually explored
        explored = any(crit_id in session["exploration_results"] for crit_id in criteria)

        if explored:
            verdicts[disorder_key] = {
                "criteria_met": criteria_met,
                "threshold": threshold,
                "diagnosis": criteria_met >= threshold,
                "has_unresolved": has_unresolved,
            }

    return verdicts


def run_gui_pipeline(session: dict, questions: dict) -> None:
    """Run all GUI phases in a single persistent window.

    Handles INIT → SELF_REPORT → EXPLORATION → (CLARIFICATION →) EVALUATION
    without closing the window between phases.
    """
    from PySide6.QtWidgets import QApplication
    from modules.gui import PipelineWindow, SelfReportGUI, ExplorationGUI
    from modules.rater import evaluate_response, evaluate_with_clarification

    app = QApplication.instance() or QApplication(sys.argv)
    window = PipelineWindow()
    window.setWindowTitle("SCID-5-PD Assessment")
    window.setMinimumSize(700, 500)
    window.show()

    def start_exploration():
        flagged = get_flagged_criteria(session, questions)
        remaining = [c for c in flagged if c["criterion_id"] not in session["exploration_results"]]

        if not remaining:
            print("No criteria to explore. Moving to evaluation.")
            session["stage"] = "EVALUATION"
            save_session(session)
            window.close()
            return

        gui = ExplorationGUI(remaining, language=session.get("language", "de"))
        state = {"phase": "exploration"}

        def on_finished(transcripts: dict):
            if state["phase"] == "exploration":
                for crit_id, transcript in transcripts.items():
                    crit_data = next((c for c in flagged if c["criterion_id"] == crit_id), None)
                    if not crit_data:
                        continue
                    print(f"Rating criterion {crit_id}...")
                    result = evaluate_response(transcript, crit_data, session.get("language", "de"))
                    result["transcript"] = transcript
                    result["clarification_transcript"] = None
                    session["exploration_results"][crit_id] = result
                    save_session(session)

                needs_clarification = []
                for crit_id, result in session["exploration_results"].items():
                    if result.get("unresolved") and not result.get("clarification_transcript"):
                        crit_data = next((c for c in flagged if c["criterion_id"] == crit_id), None)
                        if crit_data:
                            needs_clarification.append({
                                **crit_data,
                                "clarifying_question": result.get("clarifying_question"),
                            })

                if needs_clarification:
                    print(f"\n{len(needs_clarification)} criterion/criteria need clarification.")
                    state["phase"] = "clarification"
                    gui.load_criteria(needs_clarification)
                else:
                    session["stage"] = "EVALUATION"
                    save_session(session)
                    window.close()

            elif state["phase"] == "clarification":
                for crit_id, clarification_transcript in transcripts.items():
                    original_result = session["exploration_results"].get(crit_id, {})
                    original_transcript = original_result.get("transcript", "")
                    crit_data = next((c for c in flagged if c["criterion_id"] == crit_id), None)
                    if not crit_data:
                        continue
                    print(f"Re-rating criterion {crit_id} with clarification...")
                    new_result = evaluate_with_clarification(
                        original_transcript, clarification_transcript, crit_data, session.get("language", "de")
                    )
                    new_result["transcript"] = original_transcript
                    new_result["clarification_transcript"] = clarification_transcript
                    session["exploration_results"][crit_id] = new_result
                    save_session(session)

                session["stage"] = "EVALUATION"
                save_session(session)
                window.close()

        gui.finished.connect(on_finished)
        window.show_widget(gui)

    if session["stage"] == "INIT":
        self_report = SelfReportGUI(questions, session)

        def on_self_report_finished(responses):
            session["screening_responses"] = responses
            session["stage"] = "SELF_REPORT"
            save_session(session)
            flagged_count = len(get_flagged_criteria(session, questions))
            print(f"\n{flagged_count} criteria flagged for exploration.")
            session["stage"] = "EXPLORATION"
            save_session(session)
            start_exploration()

        self_report.finished.connect(on_self_report_finished)
        window.show_widget(self_report)
    else:
        # Resume from SELF_REPORT or EXPLORATION
        start_exploration()

    app.exec()


def run_evaluation(session: dict, questions: dict) -> None:
    """Stage: Compute disorder verdicts from scored criteria."""
    verdicts = compute_disorder_verdicts(session, questions)
    session["disorder_verdicts"] = verdicts
    session["stage"] = "REPORT"
    save_session(session)
    print("\nDisorder Verdicts:")
    for d, v in verdicts.items():
        status = "MEETS CRITERIA" if v["diagnosis"] else "Does not meet"
        print(f"  {d}: {v['criteria_met']}/{v['threshold']} — {status}")


def run_report(session: dict, questions: dict) -> None:
    """Stage 4: Generate clinical PDF report."""
    from modules.reporter import generate_pdf

    output_path = session_dir(session) / "report.pdf"
    generate_pdf(session, questions, output_path)
    session["stage"] = "COMPLETE"
    save_session(session)
    print(f"\nReport saved to: {output_path}")


def main():
    questions = load_questions()

    # Check for existing sessions
    sessions = list_sessions()
    session = None

    if sessions:
        incomplete = [s for s in sessions if s["stage"] != "COMPLETE"]
        if incomplete:
            print("Incomplete sessions found:")
            for i, s in enumerate(incomplete):
                print(f"  [{i}] {s['session_id']} — Stage: {s['stage']} — {s['created_at']}")
            print(f"  [n] Start new session")

            choice = input("\nResume or new? ").strip().lower()
            if choice != "n" and choice.isdigit() and int(choice) < len(incomplete):
                session = load_session(incomplete[int(choice)]["session_id"])
                print(f"Resuming session {session['session_id']} at stage {session['stage']}")

    if session is None:
        print("Starting new session...")
        session = create_session(language="de")
        print(f"Session ID: {session['session_id']}")

    # State machine
    stage = session["stage"]

    if stage in ("INIT", "SELF_REPORT", "EXPLORATION"):
        run_gui_pipeline(session, questions)
        stage = session["stage"]

    if stage == "EVALUATION":
        run_evaluation(session, questions)
        stage = session["stage"]

    if stage == "REPORT":
        run_report(session, questions)
        stage = session["stage"]

    if stage == "COMPLETE":
        print("\nSession complete!")
        print(f"Report: {session_dir(session) / 'report.pdf'}")


if __name__ == "__main__":
    main()
