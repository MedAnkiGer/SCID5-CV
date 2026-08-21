"""Run the SCID-5-CV interview in silico against a simulated patient.

This replaces the browser and the human: a persona-driven patient agent
(`modules/patient_sim.py`) answers, and the real pipeline does everything else.
Question navigation, branching, conditional wording, the rater, the
clarification loop, session state, evaluation and the PDF all come from
`app.py` — the same functions the web UI calls — so a run here exercises the
production path rather than a copy of it. Only the browser layer (audio
capture, Whisper, TTS) is bypassed.

The session it produces is an ordinary session: it appears on the home page and
can be opened, resumed, or read as a report in the web UI like any other.

Usage:
    python tools/simulate_interview.py --list-personas
    python tools/simulate_interview.py --persona mdd_moderate
    python tools/simulate_interview.py --persona panic_gad --modules Overview A F
    python tools/simulate_interview.py --persona vague_responder --start-module A --max-questions 20
    python tools/simulate_interview.py --resume 1a2b3c4d          # continue a stopped run
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as pipeline                      # noqa: E402 — the live web pipeline
from modules.patient_sim import (           # noqa: E402
    DEFAULT_MODEL as PATIENT_MODEL,
    SimulatedPatient,
    list_personas,
    load_persona,
)
from modules.rater import (                 # noqa: E402
    DEFAULT_MODEL as RATER_MODEL,
    evaluate_response,
)

OUT_OF_SCOPE = "[not simulated — module out of scope for this run]"
NO_QUESTION = (
    "[clinician judgement item — no question is put to the patient here; "
    "the clinician rates this from what has already been said]"
)


# ---------------------------------------------------------------------------
# One question
# ---------------------------------------------------------------------------

def simulate_question(session, question, patient, rater_model, max_clarifications):
    """Ask one question, rate the answer, run clarification rounds, store it.

    Returns a record of what happened, for the run log.
    """
    qid = question["id"]
    lang = session.get("language", "en")
    q_data = pipeline.q_index.get(qid, {})
    has_criterion = bool((question.get("criterion_description") or "").strip())
    spoken = (question.get("interviewer_text") or "").strip()
    started = time.time()

    record = {
        "question_id": qid,
        "module": question.get("module_id"),
        "criterion_label": question.get("criterion_label"),
        "asked": spoken,
        "exchanges": [],
        "rater_calls": 0,
    }

    # 1. Overview / intro questions carry no criterion — answered, never rated.
    if not has_criterion:
        answer = patient.answer(spoken, question_id=qid) if spoken else ""
        pipeline.store_skip(session, qid, answer)
        record.update(kind="record_only", answer=answer, score="N/A",
                      seconds=round(time.time() - started, 2))
        return record

    # 2. Clinical-judgement items have no spoken question at all — etiology
    #    rule-outs, "not better explained by...", the Module C/D differential.
    #    Nothing is asked, so there is nothing to answer and nothing to clarify.
    #    (The aggregate items that used to land here are now computed upstream
    #    by modules/aggregator.py and never reach this function.) Rate once on
    #    the marker so the item is not silently dropped, and let it show up as
    #    unresolved in the report.
    if not spoken:
        rating = evaluate_response(NO_QUESTION, q_data, lang, rater_model)
        pipeline.store_response(session, qid, NO_QUESTION, [], rating)
        record.update(kind="clinician_judgement", answer=NO_QUESTION,
                      score=rating["score"], confidence=rating.get("confidence"),
                      unresolved=rating.get("unresolved", False), rater_calls=1,
                      seconds=round(time.time() - started, 2))
        return record

    # 3. Normal clinical question: answer, rate, clarify while the rater asks.
    answer = patient.answer(spoken, question_id=qid)
    exchanges = []
    rating = None
    for round_no in range(max_clarifications + 1):
        rating = evaluate_response(
            pipeline.build_eval_text(answer, exchanges), q_data, lang, rater_model
        )
        record["rater_calls"] += 1
        wants_more = rating.get("unresolved") and rating.get("clarifying_question")
        if not wants_more or round_no >= max_clarifications:
            break
        clarifying = rating["clarifying_question"]
        follow_up = patient.answer(clarifying, question_id=qid, follow_up=True)
        exchanges.append({"question": clarifying, "answer": follow_up})

    pipeline.store_response(session, qid, answer, exchanges, rating)
    record.update(
        kind="rated",
        answer=answer,
        exchanges=exchanges,
        score=rating["score"],
        confidence=rating.get("confidence"),
        unresolved=rating.get("unresolved", False),
        rationale=rating.get("rationale", ""),
        seconds=round(time.time() - started, 2),
    )
    # Recorded on the three questions that establish whether an episode's mood
    # was elevated or only irritable; it moves the Criterion B threshold.
    if rating.get("mood_quality"):
        record["mood_quality"] = rating["mood_quality"]
    return record


# ---------------------------------------------------------------------------
# Scoring the run against the persona's ground truth
# ---------------------------------------------------------------------------

def build_scorecard(session, persona):
    responses = session["interview_responses"]
    rated = {k: r for k, r in responses.items() if r.get("score") in ("+", "-", "?")}

    expected = persona.get("expected_scores") or {}
    hits, misses, not_reached = [], [], []
    for qid, want in expected.items():
        got = responses.get(qid, {}).get("score")
        if got is None or got == "N/A":
            not_reached.append(qid)
        elif got == want:
            hits.append(qid)
        else:
            misses.append({
                "question_id": qid,
                "criterion_label": pipeline.q_index.get(qid, {}).get("criterion_label"),
                "expected": want,
                "got": got,
                "confidence": responses[qid].get("confidence"),
                "reasoning": (responses[qid].get("reasoning") or "")[:400],
                "transcript": (responses[qid].get("transcript") or "")[:400],
            })

    module_check = []
    for module in pipeline.questions["modules"]:
        mid = module["id"]
        want = (persona.get("expected_modules") or {}).get(mid)
        if not want:
            continue
        ids = [q["id"] for q in module["questions"]]
        plus = sum(1 for qid in ids if responses.get(qid, {}).get("score") == "+")
        answered = sum(1 for qid in ids if qid in responses)
        if answered == 0:
            verdict = "not reached"
        elif want == "negative":
            verdict = "ok" if plus == 0 else "FALSE POSITIVE"
        else:
            verdict = "ok" if plus > 0 else "MISSED"
        module_check.append({
            "module": mid, "expected": want, "plus": plus,
            "answered": answered, "of": len(ids), "verdict": verdict,
        })

    # Mood quality drives the Criterion B threshold, so a persona can pin it
    # down the same way it pins down a score.
    mood_check = []
    for qid, want in (persona.get("expected_mood_quality") or {}).items():
        got = responses.get(qid, {}).get("mood_quality")
        mood_check.append({
            "question_id": qid, "expected": want, "got": got,
            "verdict": "ok" if got == want else ("not reached" if got is None else "MISMATCH"),
        })

    checked = len(hits) + len(misses)
    return {
        "mood_quality": mood_check,
        "counts": {
            "+": sum(1 for r in rated.values() if r["score"] == "+"),
            "-": sum(1 for r in rated.values() if r["score"] == "-"),
            "?": sum(1 for r in rated.values() if r["score"] == "?"),
            "N/A": sum(1 for r in responses.values() if r.get("score") == "N/A"),
            "unresolved": sum(1 for r in rated.values() if r.get("unresolved")),
            "clarification_rounds": sum(
                len(r.get("exchanges") or []) for r in responses.values()
            ),
        },
        "expected_scores": {
            "checked": checked,
            "agreed": len(hits),
            "agreement": round(len(hits) / checked, 3) if checked else None,
            "not_reached": not_reached,
            "disagreements": misses,
        },
        "modules": module_check,
    }


def print_scorecard(card, persona, patient, rater_calls, elapsed):
    c = card["counts"]
    print()
    print("=" * 68)
    print(f"SCORECARD — {persona['name']}")
    print("=" * 68)
    print(f"  ratings      + {c['+']}   - {c['-']}   ? {c['?']}   "
          f"(unrated/record-only: {c['N/A']})")
    print(f"  unresolved   {c['unresolved']}   "
          f"clarification rounds used: {c['clarification_rounds']}")

    exp = card["expected_scores"]
    if exp["checked"]:
        print(f"\n  Against persona ground truth: {exp['agreed']}/{exp['checked']} "
              f"agree ({exp['agreement']:.0%})")
        for miss in exp["disagreements"]:
            print(f"    MISMATCH {miss['question_id']:<6} "
                  f"{(miss['criterion_label'] or '')[:34]:<34} "
                  f"expected {miss['expected']}  got {miss['got']}")
        if exp["not_reached"]:
            print(f"    not reached: {', '.join(exp['not_reached'])}")
    elif exp["not_reached"]:
        print(f"\n  None of the {len(exp['not_reached'])} expected items were "
              f"reached in this run — nothing to check against yet.")
    else:
        print("\n  No expected_scores in this persona — nothing to check against.")

    if card.get("mood_quality"):
        print("\n  Mood quality recorded:")
        for row in card["mood_quality"]:
            flag = "!!" if row["verdict"] == "MISMATCH" else "  "
            print(f"   {flag} {row['question_id']:<6} expected {row['expected']:<15} "
                  f"got {str(row['got']):<15} — {row['verdict']}")

    if card["modules"]:
        print("\n  Module-level expectations:")
        for row in card["modules"]:
            flag = "!!" if row["verdict"] in ("FALSE POSITIVE", "MISSED") else "  "
            print(f"   {flag} {row['module']:<9} expected {row['expected']:<9} "
                  f"{row['plus']} '+' over {row['answered']}/{row['of']} answered "
                  f"— {row['verdict']}")

    u = patient.usage_summary()
    print(f"\n  Patient  {u['model']} (effort {u['effort']}): {u['calls']} calls, "
          f"{u['input_tokens']:,} in / {u['output_tokens']:,} out"
          + (f", {u['refusals']} refused" if u["refusals"] else ""))
    print(f"  Rater    {rater_calls} calls")
    print(f"  Elapsed  {elapsed / 60:.1f} min")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Persona text and patient speech are UTF-8; a long run should not die on
    # the last print because the Windows console is on a legacy code page.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(
        description="Run the interview pipeline against a simulated patient.")
    ap.add_argument("--persona", help="Persona id or path to a persona .json")
    ap.add_argument("--list-personas", action="store_true")
    ap.add_argument("--resume", metavar="SESSION_ID",
                    help="Continue an earlier simulated session")
    ap.add_argument("--language", default="en", choices=["de", "en"])
    ap.add_argument("--patient-model", default=PATIENT_MODEL)
    ap.add_argument("--patient-effort", default="low",
                    choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--rater-model", default=RATER_MODEL)
    ap.add_argument("--modules", nargs="*", metavar="ID",
                    help="Only simulate these modules (e.g. Overview A F); the "
                         "rest are recorded as not simulated")
    ap.add_argument("--start-module", metavar="ID",
                    help="Jump straight to the first question of this module")
    ap.add_argument("--start-question", metavar="ID",
                    help="Jump straight to this question (e.g. A29)")
    ap.add_argument("--max-questions", type=int, default=0,
                    help="Stop after N simulated questions (0 = the whole interview)")
    ap.add_argument("--max-clarifications", type=int,
                    default=pipeline.MAX_CLARIFICATIONS)
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds to pause between questions (rate limiting)")
    ap.add_argument("--report", action="store_true",
                    help="Run evaluation and write the PDF even if the interview "
                         "did not finish")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print the question and the patient's answer in full")
    args = ap.parse_args()

    if args.list_personas:
        for p in list_personas():
            print(f"  {p['id']:<18} {p['name']}")
            print(f"  {'':<18} {p.get('purpose', '')[:100]}")
        return 0

    if not args.persona and not args.resume:
        ap.error("give --persona (or --resume a session started with one)")

    # --- session ----------------------------------------------------------
    if args.resume:
        session = pipeline.load_session(args.resume)
        persona_ref = args.persona or session.get("simulation", {}).get("persona_id")
        if not persona_ref:
            ap.error(f"session {args.resume} has no persona recorded; pass --persona")
        persona = load_persona(persona_ref)
        print(f"Resuming session {session['session_id']} "
              f"({len(session['interview_responses'])} questions already answered)")
    else:
        persona = load_persona(args.persona)
        session = pipeline.create_session(language=args.language)
        print(f"New session {session['session_id']}")

    session["stage"] = "INTERVIEW"
    session["simulation"] = {
        "persona_id": persona["id"],
        "persona_name": persona.get("name"),
        "persona_path": persona.get("_path"),
        "patient_model": args.patient_model,
        "rater_model": args.rater_model,
        "started_at": datetime.now().isoformat(),
    }
    pipeline.save_session(session)

    if args.start_question:
        if args.start_question not in pipeline.q_index:
            ap.error(f"unknown question {args.start_question!r}")
        session["current_question_id"] = args.start_question
        pipeline.save_session(session)
    elif args.start_module:
        first = next((q["id"] for q in pipeline.q_index.values()
                      if q.get("module_id") == args.start_module), None)
        if first is None:
            ap.error(f"unknown module {args.start_module!r}")
        session["current_question_id"] = first
        pipeline.save_session(session)

    scope = set(args.modules) if args.modules else None
    patient = SimulatedPatient(
        persona, language=session.get("language", "en"),
        model=args.patient_model, effort=args.patient_effort,
    )

    print(f"Persona  {persona['name']}")
    print(f"Patient  {args.patient_model} (effort {args.patient_effort})   "
          f"Rater  {args.rater_model}")
    if scope:
        print(f"Modules  {', '.join(sorted(scope))}")
    print("-" * 68)

    # --- interview loop ---------------------------------------------------
    records, rater_calls, simulated = [], 0, 0
    started = time.time()
    total = len(pipeline.q_order)
    finished = False

    while True:
        # Aggregate items are counted from earlier ratings, not asked. Applying
        # them here rather than letting get_current_question do it silently
        # keeps them in the run log.
        for derived in pipeline.apply_derived_ratings(session):
            records.append({
                "question_id": derived["question_id"],
                "module": pipeline.q_index[derived["question_id"]].get("module_id"),
                "criterion_label": pipeline.q_index[derived["question_id"]].get("criterion_label"),
                "kind": "derived",
                "asked": "",
                "score": derived["score"],
                "rationale": derived["rationale"],
                "unresolved": derived["unresolved"],
                "derived": derived["derived"],
                "exchanges": [],
                "rater_calls": 0,
                "seconds": 0.0,
            })
            done = len(session["interview_responses"])
            print(f"[{done:>3}/{total}] {derived['question_id']:<7} "
                  f"{records[-1]['module'] or '':<8} {derived['score']:<3} computed"
                  + ("  unresolved" if derived["unresolved"] else ""))
            if args.verbose:
                print(f"        {derived['rationale'][:200]}")

        question = pipeline.get_current_question(session)
        if question is None:
            finished = True
            break
        qid = question["id"]

        if scope is not None and question.get("module_id") not in scope:
            pipeline.store_skip(session, qid, OUT_OF_SCOPE)
            session["interview_responses"][qid]["reasoning"] = "not simulated"
            pipeline.save_session(session)
            continue

        if args.max_questions and simulated >= args.max_questions:
            break

        # The patient must hear exactly what the interviewer would say — the
        # wording of some questions depends on the previous answer.
        question = pipeline.resolve_interviewer_text(question, session)

        try:
            record = simulate_question(
                session, question, patient, args.rater_model,
                args.max_clarifications,
            )
        except KeyboardInterrupt:
            print("\nInterrupted. The session is saved and resumable:")
            print(f"  python tools/simulate_interview.py --resume {session['session_id']}")
            return 130
        except Exception as exc:                       # noqa: BLE001
            print(f"\n[ERROR] {qid}: {type(exc).__name__}: {exc}")
            print("The session is saved up to the previous question. Resume with:")
            print(f"  python tools/simulate_interview.py --resume {session['session_id']}")
            return 1

        records.append(record)
        rater_calls += record.get("rater_calls", 0)
        simulated += 1

        done = len(session["interview_responses"])
        score = record.get("score", "")
        conf = record.get("confidence")
        bits = f"[{done:>3}/{total}] {qid:<7} {question.get('module_id',''):<8} {score:<3}"
        if conf is not None:
            bits += f" conf {conf:.2f}"
        if record.get("exchanges"):
            bits += f"  +{len(record['exchanges'])} clarif"
        if record.get("unresolved"):
            bits += "  unresolved"
        bits += f"  {record['seconds']}s"
        print(bits)
        if args.verbose and record.get("asked"):
            print(f"        Q: {record['asked'][:160]}")
            print(f"        A: {(record.get('answer') or '')[:300]}")
            for ex in record.get("exchanges", []):
                print(f"        > {ex['question'][:150]}")
                print(f"        < {ex['answer'][:200]}")

        if args.sleep:
            time.sleep(args.sleep)

    elapsed = time.time() - started
    print("-" * 68)
    print("Interview complete." if finished else "Stopped early (session is resumable).")

    # --- evaluation, report, run log --------------------------------------
    if (finished or args.report) and not args.no_report:
        pipeline._run_evaluation(session)
        from modules.reporter import generate_pdf
        pdf_path = pipeline.session_dir(session) / "report.pdf"
        generate_pdf(session, pipeline.questions, pdf_path)
        print(f"Report   {pdf_path}")

    card = build_scorecard(session, persona)
    run_dir = pipeline.session_dir(session)

    # A resumed run continues an existing log rather than replacing it: the
    # question records and the per-invocation usage of earlier passes are kept.
    log_path = run_dir / "simulation.json"
    previous = {}
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            previous = json.load(f)
    all_records = previous.get("questions", []) + records
    this_run = {
        "started_at": session["simulation"]["started_at"],
        "finished_at": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "questions_simulated": len(records),
        "patient": patient.usage_summary(),
        "rater": {"model": args.rater_model, "calls": rater_calls},
    }

    run_log = {
        "session_id": session["session_id"],
        "finished": finished,
        "persona": {k: v for k, v in persona.items() if k != "_path"},
        "persona_path": persona.get("_path"),
        "max_clarifications": args.max_clarifications,
        "modules_in_scope": sorted(scope) if scope else "all",
        "runs": previous.get("runs", []) + [this_run],
        "scorecard": card,
        "questions": all_records,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(run_log, f, indent=2, ensure_ascii=False)

    with open(run_dir / "simulation_transcript.txt", "w", encoding="utf-8") as f:
        f.write(f"{persona['name']}  —  session {session['session_id']}\n")
        f.write(f"{this_run['finished_at']}\n\n")
        for r in all_records:
            if r["kind"] == "derived":
                f.write(f"[{r['question_id']}] {r.get('criterion_label') or ''}\n")
                f.write(f"  COMPUTED: {r['rationale']}\n")
                f.write(f"  -> {r['score']}"
                        f"{'  (unresolved)' if r.get('unresolved') else ''}\n\n")
                continue
            if r["kind"] == "clinician_judgement":
                f.write(f"[{r['question_id']}] (clinician judgement, "
                        f"nothing asked) -> {r.get('score')}\n\n")
                continue
            f.write(f"[{r['question_id']}] {r.get('criterion_label') or ''}\n")
            f.write(f"  INTERVIEWER: {r['asked']}\n")
            f.write(f"  PATIENT:     {r.get('answer', '')}\n")
            for ex in r.get("exchanges", []):
                f.write(f"  INTERVIEWER: {ex['question']}\n")
                f.write(f"  PATIENT:     {ex['answer']}\n")
            if r.get("score") and r["score"] != "N/A":
                f.write(f"  -> {r['score']}"
                        f"{'  (unresolved)' if r.get('unresolved') else ''}\n")
            f.write("\n")

    print_scorecard(card, persona, patient, rater_calls, elapsed)
    print(f"\n  Run log  {run_dir / 'simulation.json'}")
    print(f"  Dialogue {run_dir / 'simulation_transcript.txt'}")
    print(f"  In the web UI: /session/{session['session_id']}/report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
