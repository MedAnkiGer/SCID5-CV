# SCID-5 PD AI Pipeline

## Project Overview
AI-assisted SCID-5 Personality Disorder diagnostic pipeline. Guides a patient through:
1. **Stage 1** - 106-item self-report screening (PySide6 GUI)
2. **Stage 2** - AI voice exploration + Whisper transcription for flagged items
3. **Stage 3** - LLM scoring via Claude API (structured JSON output)
4. **Stage 4** - Clinical PDF report generation

## Architecture
- `main.py` — State machine orchestrator (INIT → SELF_REPORT → EXPLORATION → EVALUATION → REPORT → COMPLETE)
- `modules/gui.py` — PySide6 self-report questionnaire
- `modules/exploration_engine.py` — Audio recording + Whisper transcription
- `modules/rater.py` — Claude API clinical scoring
- `modules/reporter.py` — PDF report generation with fpdf2
- `data/questions.json` — Question bank (mock v0.1, real questions added later)
- `data/sessions/{session_id}/state.json` — Per-session state (resumable)
- `prompts/rater_system_prompt.txt` — Clinical system prompt for Claude
- `tools/question_entry.py` — CLI helper to add real questions

## Key Conventions
- All text fields are bilingual: `_de` and `_en` suffixes
- Session state is saved after every step for crash resilience
- Audio is never saved to disk (privacy)
- Scoring: 0 (Absent), 1 (Sub-threshold), 2 (Threshold) per DSM-5
- One clarification attempt per criterion max

## Dependencies
python-dotenv, anthropic, openai, sounddevice, scipy, fpdf2, PySide6
