# SCID-5 Personality AI Wrapper Project

## 1. Project Overview
An AI-led diagnostic tool for the SCID-5 Personality Disorder (PD) module.
* **Workflow:** GUI Self-Report -> Filtered AI Voice Exploration -> LLM Scoring -> PDF Reporting.
* **Language Stack:** Python (Conda), OpenAI Whisper API (STT), Anthropic API (Rater).
* **Core Loop:** Stage 1 GUI -> Logic Gate (Yes/No) -> Stage 2 Interviewer -> Rater Analysis -> Final Verdict.

## 2. Clinical Guardrails
* **Scoring Scale:** 0 (Absent), 1 (Sub-threshold), 2 (Threshold).
* **Branching Rule:** If a screening question (Self-Report) is "No" (0), skip the associated exploration module.
* **Clarification Loop:** If LLM confidence is < 0.7 or status is 'ambiguous', the AI must trigger a predefined or generative clarifying follow-up.
* **DSM-5 Fidelity:** Ratings must align with DSM-5 diagnostic criteria for Cluster A, B, and C.

## 3. System Architecture
* **Stage 1 (Self-Report):** 106-question GUI (Tkinter/PySide6). Stores binary results in `data/session_state.json`.
* **Stage 2 (Exploration):** Filters "Yes" answers. For each, the AI reads a predefined question from the PDF source.
* **Stage 3 (Evaluation):** Audio -> Whisper API (German/English) -> Claude Rater Agent (0/1/2 scoring).
* **Stage 4 (Reporting):** Generate a clinical PDF summary with transcripts, evidence quotes, and final verdicts.

## 4. Technical Rules
* **Environment:** Use `scid-env` Conda environment.
* **API Strategy:** Minimize local CPU load. Use OpenAI Whisper API for all STT tasks.
* **Bilingualism:** Handle German (`de`) and English (`en`). Explicitly set `language` in Whisper calls.
* **Privacy:** Never log raw audio; only store anonymized transcripts and JSON scores.

## 5. Coding Style & Setup
* **Style:** Follow PEP 8 guidelines and use a modular design.
* **Files:** `gui.py` (Stage 1), `exploration_engine.py` (Stage 2), `rater.py` (Evaluation logic).
* **Safety:** Keys must be loaded via `os.getenv()` from a local `.env` file.
* **Responses:** All AI components must output structured JSON: `{"score": 0|1|2, "rationale": "...", "unresolved": bool}`.

## 6. Setup Instructions
* Find `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` in `.env` file.
* The code uses `os.getenv()` to load these safely.