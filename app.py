"""SCID-5-CV Web Application — FastAPI browser interface.

Replaces the console-based interview with a browser UI.
All backend logic (rater, reporter, exploration_engine) unchanged.

Run:  uvicorn app:app --reload --port 8000
"""

import io
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from modules.aggregator import derive_rating

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env", override=True)

app = FastAPI(title="SCID-5-CV Assessment")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))

DATA_DIR = Path(__file__).resolve().parent / "data"
QUESTIONS_PATH = DATA_DIR / "questions.json"
SESSIONS_DIR = DATA_DIR / "sessions"

MAX_CLARIFICATIONS = 3


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_questions() -> dict:
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_question_index(qs: dict) -> tuple[dict, list[str]]:
    index, order = {}, []
    for module in qs["modules"]:
        for q in module["questions"]:
            q = dict(q)
            q["module_id"] = module["id"]
            q["module_name_en"] = module.get("name_en", "")
            # Normalise interviewer_text — some questions have conditional variants
            if not q.get("interviewer_text"):
                q["interviewer_text"] = (
                    q.get("interviewer_text_if_previous_plus")
                    or q.get("interviewer_text_if_previous_minus")
                    or ""
                )
            index[q["id"]] = q
            order.append(q["id"])
    return index, order


def next_question_id(current_id, score, q_data, order):
    skip_if = q_data.get("skip_if")
    score_str = str(score)
    aliases = {score_str}
    if score_str == "+":
        aliases |= {"YES", "yes", "1", "true"}
    elif score_str == "-":
        aliases |= {"NO", "no", "0", "false"}
    if isinstance(skip_if, dict):
        for alias in aliases:
            if alias in skip_if and skip_if[alias] in set(order):
                return skip_if[alias]
    try:
        idx = order.index(current_id)
    except ValueError:
        return None
    return order[idx + 1] if idx + 1 < len(order) else None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def session_dir(s): return SESSIONS_DIR / s["session_id"]


def save_session(s):
    d = session_dir(s)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "state.json", "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


def load_session(sid):
    with open(SESSIONS_DIR / sid / "state.json", encoding="utf-8") as f:
        return json.load(f)


def list_sessions():
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for d in SESSIONS_DIR.iterdir():
        p = d / "state.json"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                sessions.append(json.load(f))
    sessions.sort(key=lambda s: s["created_at"], reverse=True)
    return sessions


def create_session(language="en"):
    s = {
        "session_id": str(uuid.uuid4())[:8],
        "created_at": datetime.now().isoformat(),
        "language": language,
        "stage": "INIT",
        "current_question_id": None,
        "interview_responses": {},
        "module_summaries": {},
    }
    save_session(s)
    return s


# ---------------------------------------------------------------------------
# Question navigation
# ---------------------------------------------------------------------------

questions = load_questions()
q_index, q_order = build_question_index(questions)


def _next_unanswered(session):
    """Return the next unanswered question dict (with progress info), or None."""
    responses = session["interview_responses"]
    # Resume from current pointer, skip answered
    start = session.get("current_question_id") or q_order[0]
    started = False
    for qid in q_order:
        if qid == start:
            started = True
        if not started:
            continue
        if qid in responses:
            # Follow branching from the stored score
            score = responses[qid].get("score", "-")
            q_data = q_index.get(qid, {})
            nxt = next_question_id(qid, score, q_data, q_order)
            if nxt and nxt != q_order[q_order.index(qid) + 1] if q_order.index(qid) + 1 < len(q_order) else None:
                # There's a skip — jump ahead
                started = False  # reset, find nxt
                for i, oid in enumerate(q_order):
                    if oid == nxt:
                        started = True
                    if started and oid not in responses:
                        q = dict(q_index[oid])
                        q["_progress_done"] = len(responses)
                        q["_progress_total"] = len(q_order)
                        return q
                return None  # all answered after skip
            continue
        # Found unanswered question
        q = dict(q_index[qid])
        q["_progress_done"] = len(responses)
        q["_progress_total"] = len(q_order)
        return q
    return None


def apply_derived_ratings(session):
    """Score any aggregate items sitting at the current position.

    These questions ask the patient nothing — they count how other criteria were
    rated (see `modules/aggregator.py`). Computing them here means neither the
    clinician nor the simulator is ever presented with one.

    Returns the ratings applied, newest last, for logging.
    """
    applied = []
    while True:
        q = _next_unanswered(session)
        if q is None:
            break
        rating = derive_rating(q["id"], session["interview_responses"])
        if rating is None:
            break
        summary = rating.pop("summary")
        store_response(session, q["id"], summary, [], rating)
        applied.append({"question_id": q["id"], **rating})
    return applied


def get_current_question(session):
    """Return the question to put to the patient next, or None if finished.

    Aggregate items are computed and stored on the way past, so a caller never
    sees one. This writes to the session when that happens.
    """
    apply_derived_ratings(session)
    return _next_unanswered(session)


def walk_interview_order(session):
    """Walk through all questions following branching, yield (qid, q_data) for answered questions."""
    responses = session["interview_responses"]
    idx = 0
    while idx < len(q_order):
        qid = q_order[idx]
        q_data = q_index.get(qid, {})
        if qid in responses:
            yield qid, q_data, responses[qid]
            score = responses[qid].get("score", "-")
            nxt = next_question_id(qid, score, q_data, q_order)
            if nxt and nxt in set(q_order):
                idx = q_order.index(nxt)
            else:
                idx += 1
        else:
            break


def resolve_interviewer_text(q, session):
    """Pick the right interviewer_text variant for a question.

    Some questions are worded differently depending on how the previous one was
    scored. Returns a copy with `interviewer_text` set to the variant actually
    spoken to the patient — this is the single source of truth for what the
    patient hears, used by the interview page and by the simulator.
    """
    idx = q_order.index(q["id"])
    if idx > 0:
        prev_id = q_order[idx - 1]
        prev_score = session["interview_responses"].get(prev_id, {}).get("score", "-")
    else:
        prev_score = "-"

    if prev_score == "+" and q.get("interviewer_text_if_previous_plus"):
        q = dict(q)
        q["interviewer_text"] = q["interviewer_text_if_previous_plus"]
    elif prev_score == "-" and q.get("interviewer_text_if_previous_minus"):
        q = dict(q)
        q["interviewer_text"] = q["interviewer_text_if_previous_minus"]
    return q


# ---------------------------------------------------------------------------
# Answer handling — shared by the web routes and tools/simulate_interview.py
# ---------------------------------------------------------------------------

def build_eval_text(transcript, exchanges):
    """Flatten an initial answer plus clarification rounds into rater input."""
    if not exchanges:
        return transcript
    parts = [f"[Initial answer]\n{transcript}"]
    for i, ex in enumerate(exchanges, 1):
        parts.append(f"[Follow-up question {i}]\n{ex['question']}")
        parts.append(f"[Follow-up answer {i}]\n{ex['answer']}")
    return "\n\n".join(parts)


def store_response(session, qid, transcript, exchanges, rating):
    """Save a rated answer and advance the session pointer."""
    response = {
        "score": rating["score"],
        "transcript": transcript,
        "exchanges": exchanges,
        "unresolved": rating.get("unresolved", False),
        "reasoning": rating.get("rationale", "") or rating.get("reasoning", ""),
        "confidence": rating.get("confidence"),
    }
    # Aggregate items carry the arithmetic that produced them, so the report can
    # show that the score was counted rather than rated.
    if rating.get("derived"):
        response["derived"] = rating["derived"]
    session["interview_responses"][qid] = response
    session["current_question_id"] = qid
    save_session(session)


def store_skip(session, qid, transcript):
    """Save an unrated answer (overview/intro question) and advance."""
    session["interview_responses"][qid] = {
        "score": "N/A",
        "transcript": transcript,
        "exchanges": [],
        "unresolved": False,
        "reasoning": "",
    }
    session["current_question_id"] = qid
    save_session(session)


# ---------------------------------------------------------------------------
# Routes — Sessions
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request, "sessions": list_sessions()
    })


@app.post("/session/new")
async def new_session(request: Request):
    form = await request.form()
    s = create_session(language=form.get("language", "en"))
    return RedirectResponse(f"/session/{s['session_id']}", status_code=303)


@app.post("/session/{sid}/archive")
async def archive_session(sid: str):
    """Move session to archive (hidden from UI, data retained)."""
    import shutil
    src = SESSIONS_DIR / sid
    dst = SESSIONS_DIR / "archived" / sid
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return RedirectResponse("/", status_code=303)


@app.post("/session/{sid}/interview/finish")
async def interview_finish(sid: str):
    """Skip all remaining questions and jump straight to the report."""
    session = load_session(sid)
    _run_evaluation(session)
    return RedirectResponse(f"/session/{sid}/report", status_code=303)


@app.post("/session/{sid}/interview/skip-module")
async def skip_module(sid: str):
    """Jump to the first unanswered question in the next module."""
    session = load_session(sid)
    current_q = get_current_question(session)
    if current_q is None:
        return RedirectResponse(f"/session/{sid}/interview", status_code=303)

    current_module = current_q["module_id"]
    # Search forward from the current question for the first question of a later module
    current_idx = q_order.index(current_q["id"])
    for qid in q_order[current_idx:]:
        if q_index[qid]["module_id"] != current_module:
            session["current_question_id"] = qid
            save_session(session)
            break
    return RedirectResponse(f"/session/{sid}/interview", status_code=303)


@app.get("/session/{sid}")
async def session_dispatch(sid: str):
    s = load_session(sid)
    stage = s["stage"]
    if stage in ("INIT", "INTERVIEW"):
        return RedirectResponse(f"/session/{sid}/interview")
    elif stage == "EVALUATION":
        return RedirectResponse(f"/session/{sid}/report")
    return RedirectResponse(f"/session/{sid}/report")


# ---------------------------------------------------------------------------
# Routes — Interview (one question at a time)
# ---------------------------------------------------------------------------

@app.get("/session/{sid}/interview", response_class=HTMLResponse)
async def interview_get(request: Request, sid: str):
    session = load_session(sid)
    session["stage"] = "INTERVIEW"
    save_session(session)

    q = get_current_question(session)
    if q is None:
        session["stage"] = "EVALUATION"
        _run_evaluation(session)
        return RedirectResponse(f"/session/{sid}/report", status_code=303)

    lang = session.get("language", "en")
    has_criterion = bool((q.get("criterion_description") or "").strip())

    # For questions with conditional interviewer text, pick the right variant
    # based on the previous question's score.
    q = resolve_interviewer_text(q, session)

    # --- new UI (additive) -------------------------------------------------
    use_new_ui = request.query_params.get("ui", "v2") != "old"
    shoulder_safe = request.query_params.get("safe") == "1"

    module_items, module_answered = [], 0
    if use_new_ui:
        for module in questions["modules"]:
            if module["id"] == q["module_id"]:
                module_items = module["questions"]
                module_answered = sum(
                    1 for it in module_items
                    if it["id"] in session["interview_responses"]
                )
                break

    return templates.TemplateResponse(
        "interview_v2.html" if use_new_ui else "interview.html", {
            "request": request,
            "session": session,
            "question": q,
            "lang": lang,
            "has_criterion": has_criterion,
            "max_clarifications": MAX_CLARIFICATIONS,
            "module_items": module_items,
            "module_answered": module_answered,
            "shoulder_safe": shoulder_safe,
        })


@app.post("/session/{sid}/interview/transcribe")
async def transcribe(sid: str, audio: UploadFile):
    session = load_session(sid)
    lang = session.get("language", "en")
    raw = await audio.read()
    # Determine file extension from content type (browser sends webm or ogg)
    ct = audio.content_type or ""
    if "ogg" in ct:
        ext = "ogg"
    elif "webm" in ct:
        ext = "webm"
    else:
        ext = "wav"
    # Save audio to disk under session/audio/{qid}_{criterion_label}_{timestamp}.{ext}
    q = get_current_question(session)
    qid = q["id"] if q else "unknown"
    label = (q.get("criterion_label") or "") if q else ""
    slug = "".join(c if c.isalnum() else "_" for c in label).strip("_") if label else ""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    audio_dir = session_dir(session) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if slug:
        filename = f"{qid}_{slug}_{ts}.{ext}"
    else:
        n = len(list(audio_dir.glob("overview_*"))) + 1
        filename = f"overview_{n}_{qid}_{ts}.{ext}"
    (audio_dir / filename).write_bytes(raw)
    from modules.exploration_engine import transcribe_audio_bytes
    transcript = transcribe_audio_bytes(raw, ext=ext, language=lang)
    (audio_dir / filename).with_suffix(".txt").write_text(transcript, encoding="utf-8")
    return JSONResponse({"transcript": transcript})


@app.post("/session/{sid}/interview/tts")
async def tts_endpoint(request: Request, sid: str):
    """Generate TTS audio and return as WAV bytes for browser playback."""
    body = await request.json()
    text = body.get("text", "")
    session = load_session(sid)
    lang = session.get("language", "en")
    if not text.strip():
        return JSONResponse({"error": "empty text"}, status_code=400)

    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.audio.speech.create(
        model="tts-1", voice="nova", input=text, response_format="mp3",
    )
    from fastapi.responses import Response
    return Response(content=response.content, media_type="audio/mpeg")


@app.post("/session/{sid}/interview/skip")
async def interview_skip(request: Request, sid: str):
    """Skip a question without criterion (overview/intro)."""
    session = load_session(sid)
    form = await request.form()
    qid = form.get("question_id")
    transcript = form.get("transcript", "").strip()

    store_skip(session, qid, transcript)
    return RedirectResponse(f"/session/{sid}/interview", status_code=303)


@app.post("/session/{sid}/interview/rate")
async def interview_rate(request: Request, sid: str):
    """Rate a clinical question: initial rating or clarification round."""
    from modules.rater import evaluate_response

    session = load_session(sid)
    form = await request.form()
    qid = form.get("question_id")
    transcript = form.get("transcript", "").strip()
    clarification_round = int(form.get("clarification_round", "0"))
    exchanges_json = form.get("exchanges", "[]")
    exchanges = json.loads(exchanges_json)

    lang = session.get("language", "en")
    q_data = q_index.get(qid, {})

    # Build full context for multi-round clarification
    eval_text = build_eval_text(transcript, exchanges)

    rating = evaluate_response(eval_text, q_data, lang)

    # If unresolved and rounds left, return the rating for client to show clarification UI
    if rating.get("unresolved") and rating.get("clarifying_question") and clarification_round < MAX_CLARIFICATIONS:
        return JSONResponse({
            "status": "needs_clarification",
            "score": rating["score"],
            "confidence": rating["confidence"],
            "rationale": rating.get("rationale", ""),
            "clarifying_question": rating["clarifying_question"],
            "clarification_round": clarification_round + 1,
        })

    # Final result — save and advance
    if rating.get("unresolved"):
        rating["_note"] = "max clarifications reached"

    store_response(session, qid, transcript, exchanges, rating)

    return JSONResponse({"status": "done", **rating})


@app.post("/session/{sid}/interview/accept")
async def interview_accept(request: Request, sid: str):
    """Accept the final rating and advance to the next question."""
    return RedirectResponse(f"/session/{sid}/interview", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Overview + Patient display (Modernist UI, additive)
# ---------------------------------------------------------------------------

@app.get("/session/{sid}/overview", response_class=HTMLResponse)
async def overview_get(request: Request, sid: str):
    session = load_session(sid)
    responses = session["interview_responses"]
    current = get_current_question(session)
    current_mid = current["module_id"] if current else None

    plus = sum(1 for r in responses.values() if r.get("score") == "+")
    minus = sum(1 for r in responses.values() if r.get("score") == "-")
    unknown = sum(1 for r in responses.values() if r.get("score") == "?")
    unresolved = sum(1 for r in responses.values() if r.get("unresolved"))
    rounds = sum(len(r.get("exchanges") or []) for r in responses.values())
    rated = plus + minus + unknown

    module_rows = []
    for module in questions["modules"]:
        items = module["questions"]
        ticks, answered = [], 0
        reached = False
        for it in items:
            resp = responses.get(it["id"])
            if resp:
                answered += 1
                reached = True
                score = resp.get("score")
                ticks.append({"+": "plus", "-": "minus", "?": "unknown"}.get(score, "minus"))
            elif current and it["id"] == current["id"]:
                ticks.append("current")
                reached = True
            elif reached and module["id"] == current_mid:
                ticks.append("skipped")
            else:
                ticks.append("todo")
        if module["id"] == current_mid:
            note = "In progress"
        elif answered == len(items):
            note = "Complete"
        elif answered:
            note = "Partly answered"
        else:
            note = "Not started"
        module_rows.append({
            "id": module["id"],
            "name": module.get("name_en", module["id"]),
            "total": len(items),
            "answered": answered,
            "ticks": ticks,
            "note": note,
            "is_current": module["id"] == current_mid,
        })

    total = len(q_order)
    done = len(responses)
    return templates.TemplateResponse("overview.html", {
        "request": request,
        "session": session,
        "current": current,
        "done": done,
        "total": total,
        "skipped": 0,
        "pct_done": int(done / total * 100) if total else 0,
        "pct_skipped": 0,
        "plus": plus, "minus": minus, "unknown": unknown,
        "rated": rated, "unresolved": unresolved, "rounds": rounds,
        "module_rows": module_rows,
        "max_clarifications": MAX_CLARIFICATIONS,
    })


@app.get("/session/{sid}/patient", response_class=HTMLResponse)
async def patient_display(request: Request, sid: str):
    session = load_session(sid)
    return templates.TemplateResponse("patient.html", {
        "request": request, "session": session,
    })


@app.get("/session/{sid}/patient/current")
async def patient_current(sid: str):
    """Only what the patient may see: the question, in plain language."""
    session = load_session(sid)
    q = get_current_question(session)
    if q is None:
        return JSONResponse({"question_id": None, "text": "Thank you — we are done.",
                             "notes_patient": "", "part": ""})
    mods = [m["id"] for m in questions["modules"]]
    idx = mods.index(q["module_id"]) + 1 if q["module_id"] in mods else 1
    return JSONResponse({
        "question_id": q["id"],
        "text": q.get("interviewer_text", ""),
        "notes_patient": "",          # never send `notes` — those are interviewer prompts
        "part": f"Part {idx} of {len(mods)}",
    })


# ---------------------------------------------------------------------------
# Evaluation + Report
# ---------------------------------------------------------------------------

def _run_evaluation(session):
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


@app.get("/session/{sid}/report", response_class=HTMLResponse)
async def report_get(request: Request, sid: str):
    session = load_session(sid)
    if not session.get("module_summaries"):
        _run_evaluation(session)
    # Auto-save PDF on first visit to the report page
    out = session_dir(session) / "report.pdf"
    if not out.exists():
        from modules.reporter import generate_pdf
        generate_pdf(session, questions, out)
        session["stage"] = "COMPLETE"
        save_session(session)
    responses = session["interview_responses"]
    rated_items = {k: r for k, r in responses.items() if r.get("score") != "N/A"}
    plus = sum(1 for r in rated_items.values() if r.get("score") == "+")
    unresolved = sum(1 for r in rated_items.values() if r.get("unresolved"))

    module_flags = {}
    for module in questions["modules"]:
        module_flags[module["id"]] = sum(
            1 for q in module["questions"]
            if (responses.get(q["id"]) or {}).get("unresolved")
        )

    use_new_ui = request.query_params.get("ui", "v2") != "old"
    return templates.TemplateResponse(
        "report_v2.html" if use_new_ui else "report.html", {
            "request": request,
            "session": session,
            "modules": questions["modules"],
            "q_index": q_index,
            "rated": len(rated_items),
            "plus": plus,
            "unresolved": unresolved,
            "module_flags": module_flags,
        })


@app.get("/session/{sid}/report/pdf")
async def report_pdf(sid: str):
    session = load_session(sid)
    from modules.reporter import generate_pdf
    out = session_dir(session) / "report.pdf"
    if not out.exists():
        generate_pdf(session, questions, out)
        session["stage"] = "COMPLETE"
        save_session(session)
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"SCID5CV_{sid}.pdf")
