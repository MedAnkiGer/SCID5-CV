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
- `data/questions.json` — 378 questions across 11 modules
- `data/sessions/{session_id}/state.json` — Per-session state (resumable)
- `prompts/rater_system_prompt.txt` — Clinical system prompt for Claude
- `templates/` — Jinja2 HTML templates (base, index, interview, report)
- `static/recorder.js` — Browser MediaRecorder wrapper
- `start_web.bat` — Windows launcher for uvicorn dev server

## Key Conventions
- All text fields are bilingual: `_de` and `_en` suffixes
- Session state is saved after every step for crash resilience
- Audio is never saved to disk (privacy) — recorded in browser, transcribed server-side
- Scoring: + (present), - (absent), ? (inadequate info) per DSM-5
- Up to 3 clarification rounds per question
- Questions without `criterion_description` are overview/intro — skip-only, no rating

## Running
```
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```
Or double-click `start_web.bat` on Windows.

## Dependencies
python-dotenv, anthropic, openai, fastapi, uvicorn, jinja2, python-multipart, fpdf2
