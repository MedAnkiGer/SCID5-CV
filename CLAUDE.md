# SCID-5-CV AI Pipeline

## Project Overview
AI-assisted SCID-5 Clinician Version diagnostic interview pipeline. Browser-based web UI that guides a patient through:
1. **Interview** — 378 questions across 11 modules (Overview, A–J), with branching via `skip_if`
2. **AI Rating** — Per-question Claude API scoring (+/-/?) against DSM-5 criteria, with up to 3 clarification rounds
3. **Report** — Module summary + detailed findings, exportable as PDF

## Architecture
- `app.py` — FastAPI web application (routes, session management, question navigation)
- `main.py` — Original console-based orchestrator (kept for reference)
- `modules/exploration_engine.py` — Whisper transcription (`transcribe_audio`)
- `modules/rater.py` — Claude API clinical scoring (`evaluate_response`)
- `modules/reporter.py` — PDF report generation with fpdf2
- `modules/aggregator.py` — Scores the count-only items from other ratings (`derive_rating`)
- `modules/patient_sim.py` — Simulated patient (`SimulatedPatient`) for in-silico runs
- `tools/simulate_interview.py` — Headless driver: runs a whole interview against a persona
- `data/questions.json` — 378 questions across 11 modules
- `data/derived_rules.json` — Counting rules for the 17 aggregate items (quotes the criterion text)
- `data/personas/*.json` — Simulated patients + their ground-truth `expected_scores`
- `data/sessions/{session_id}/state.json` — Per-session state (resumable)
- `prompts/rater_system_prompt.txt` — Clinical system prompt for Claude
- `prompts/patient_system_prompt.txt` — Role instructions for the simulated patient
- `templates/` — Jinja2 HTML templates (base, index, interview, report)
- `static/recorder.js` — Browser MediaRecorder wrapper
- `start_web.bat` — Windows launcher for uvicorn dev server

## Key Conventions
- All text fields are bilingual: `_de` and `_en` suffixes
- Session state is saved after every step for crash resilience
- Audio IS saved to disk — recorded in browser, transcribed server-side, then written to
  `data/sessions/{session_id}/audio/{qid}_{criterion}_{timestamp}.{ext}` with the transcript
  as a sibling `.txt`. Do not remove this; the recordings are study data.
- Privacy is preserved by pseudonymization, not by discarding: `session_id` is a random 8-char
  UUID, filenames carry only question id / criterion label / UTC timestamp (no patient
  identifiers), and `data/sessions/` is gitignored so recordings never enter the repo.
  Any re-identification key is kept outside this codebase.
- Scoring: + (present), - (absent), ? (inadequate info) per DSM-5
- Up to 3 clarification rounds per question
- Questions without `criterion_description` are overview/intro — skip-only, no rating
- 45 clinical questions have no `interviewer_text` at all — nothing is asked of the patient.
  They split into two kinds:
  - **Aggregate items** (17, e.g. A10 "At least five criteria met", H22, G41) only count how
    other criteria were rated. `modules/aggregator.py` computes them from
    `data/derived_rules.json` and `apply_derived_ratings()` stores them as the interview walks
    past, so neither the clinician nor the simulator is ever shown one. They never reach the
    rater — there is no transcript, so it could only ever return "?". A derived response carries
    a `derived` block with the arithmetic, and its `transcript` starts with `[computed]`
  - **Clinical-judgement items** (the rest: etiology rule-outs, "not better explained by
    another mental disorder", the Module C/D differential) weigh evidence rather than count it.
    These are still unhandled — they reach the rater with no transcript and come back "?"
- An aggregate scores "-" only when even the unrated and "?" members could not carry it over
  the threshold; otherwise it is "?" and unresolved. Never assert a threshold over criteria
  that were not assessed
- Answer handling lives in `app.py` as plain functions — `resolve_interviewer_text`,
  `build_eval_text`, `store_response`, `store_skip` — shared by the web routes and the
  simulator. Put new interview logic there rather than inside a route, so both paths use it

## Running
```
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```
Or double-click `start_web.bat` on Windows.

## Testing in silico
`tools/simulate_interview.py` runs the real pipeline against a simulated patient — no browser,
no microphone, no human. It reuses `app.py`'s navigation, branching, rating and report code, so
a run exercises the production path; only audio capture, Whisper and TTS are bypassed. The
sessions it writes are ordinary sessions and open normally in the web UI.

```
python tools/simulate_interview.py --list-personas
python tools/simulate_interview.py --persona mdd_moderate                 # full interview
python tools/simulate_interview.py --persona panic_gad --modules Overview A F
python tools/simulate_interview.py --persona vague_responder --start-module A --max-questions 20
python tools/simulate_interview.py --resume <session_id>                  # continue where it stopped
```

- Patient model defaults to `claude-opus-5` at effort `low`; `--patient-model` / `--rater-model`
  override. A full run is ~380 patient calls plus ~380+ rater calls, so scope runs while iterating
- **The patient agent must never be shown clinician-only fields** (`criterion_description`,
  `notes`, `rating_options`, rater rationale). It hears only `interviewer_text`, the same as a
  real patient. Leaking those makes the test circular and flatters the rater
- Ground truth lives in the persona (`expected_scores` per question, `expected_modules` per
  module). The run prints a scorecard against it and writes `simulation.json` (full run log with
  usage and per-question records) and `simulation_transcript.txt` (readable dialogue) into the
  session directory
- Add a case by dropping another JSON file in `data/personas/` — see the four existing ones for
  the schema (`clinical_facts`, `absent`, `style`, `expected_scores`, `expected_modules`)

## Dependencies
python-dotenv, anthropic, openai, fastapi, uvicorn, jinja2, python-multipart, fpdf2
