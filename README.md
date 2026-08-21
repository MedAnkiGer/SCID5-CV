# SCID-5 Personality Disorder AI Wrapper

An AI-led diagnostic tool for the SCID-5 Personality Disorder (PD) module with a self-report GUI and LLM-based clinical evaluation.

## Project Structure

```
P_SCID_PD_by_AI/
├── main.py                 # Main state machine orchestrator
├── modules/
│   ├── __init__.py
│   ├── gui.py             # Stage 1: Self-Report GUI (106 questions)
│   ├── exploration_engine.py  # Stage 2: AI Voice Exploration (TODO)
│   ├── rater.py           # Stage 3: LLM Scoring Agent (TODO)
│   └── reporter.py        # Stage 4: PDF Report Generation (TODO)
├── data/
│   └── session_state.json  # Persistent session data
├── requirements.txt        # Python dependencies
├── .env.example           # Template for environment variables
├── claude.md              # Project specifications
└── README.md              # This file
```

## Workflow Overview

1. **Stage 1 (Self-Report)**: User completes 106-question GUI screening
2. **Stage 2 (Exploration)**: AI explores "Yes" answers via voice
3. **Stage 3 (Evaluation)**: LLM rater scores responses (0/1/2)
4. **Stage 4 (Reporting)**: Generate clinical PDF summary

## Setup Instructions

### Prerequisites
- Python 3.9+ with Conda environment manager
- OpenAI API key (for Whisper STT)
- Anthropic API key (for Claude Rater)

### 1. Create Conda Environment

```bash
conda create -n scid-env python=3.11
conda activate scid-env
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API Keys

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

Edit `.env`:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
OPENAI_API_KEY=sk-your-key-here
```

### 4. Run the Application

```bash
python main.py
```

This will:
1. Initialize a new session (or load existing)
2. Launch the Stage 1 Self-Report GUI
3. Save responses to `data/session_state.json`

## Development Notes

### Coding Style
- Follow PEP 8 guidelines
- Use type hints where applicable
- Modular design with clear separation of concerns

### API Responses
All AI components output structured JSON:
```json
{
  "score": 0,
  "rationale": "...",
  "unresolved": false
}
```

### Scoring Scale
- **0 (Absent)**: Criterion not met
- **1 (Sub-threshold)**: Criterion partially met
- **2 (Threshold)**: Criterion met

### Branching Rule
If a screening question (Stage 1) is "No" (0), skip exploration for that item.

## Computed (Aggregate) Items

Seventeen questions in the booklet ask the patient nothing — they only count how other criteria
were rated ("AT LEAST FIVE OF THE ABOVE CRITERION A SXS (A1–A9) ARE RATED '+'"). The pipeline
computes these instead of sending them to the rater, which has no transcript to work from and
could only return "?". The rules are in `data/derived_rules.json`, each quoting the criterion text
it encodes so the arithmetic can be checked against the booklet without reading code.

A computed item scores `-` only when even the unrated and `?` members could not carry it over the
threshold; otherwise it comes back `?` and flagged, naming the criteria that were never settled.
Where DSM-5 raises a threshold for irritable-only mood, a count that clears the lower threshold
but not the higher one is scored `+` and flagged for clinician review rather than guessed at.

## In-Silico Testing (Simulated Patient)

A second pipeline supplies the answers, so the interview can be run end to end without a
participant. A persona file describes a case; a Claude-driven patient agent answers each question
in character; the ordinary rating, clarification, evaluation and report code does the rest.

```bash
python tools/simulate_interview.py --list-personas
python tools/simulate_interview.py --persona mdd_moderate            # full 378-question run
python tools/simulate_interview.py --persona panic_gad --modules Overview A F
python tools/simulate_interview.py --resume <session_id>             # continue a stopped run
```

Four cases ship in `data/personas/`: a healthy control (everything should score `-`), a moderate
major depressive episode, panic disorder with generalised worry, and a guarded/vague informant
with the same depression as the second case — the last one exists to exercise the clarification
loop.

Each persona carries its own ground truth (`expected_scores`, `expected_modules`), so a run ends
with a scorecard: agreement per question, false positives and misses per module, clarification
rounds used, and token spend. The session it produces is a normal session and opens in the web UI
like any other; `simulation.json` and `simulation_transcript.txt` in the session directory hold
the full run log and the readable dialogue.

The simulated patient is shown only what a real patient would hear — the interviewer's spoken
question. It never sees the DSM-5 criterion text, the clinician notes, or the rater's reasoning.

## Clinical Guardrails

- **DSM-5 Fidelity**: Ratings align with diagnostic criteria
- **Confidence Tracking**: LLM confidence < 0.7 triggers clarification loop
- **Privacy**: Recordings and transcripts are retained as study data under
  `data/sessions/{session_id}/audio/`, stored locally and pseudonymously — the session id is a
  random 8-char UUID and filenames contain no patient identifiers. `data/sessions/` is gitignored,
  so no recording is ever committed. Audio is sent to the OpenAI Whisper API for transcription and
  transcripts to the Anthropic API for rating; both are third-party processors and must be covered
  by your study's consent and data-processing agreements.
- **Bilingual Support**: German and English via Whisper API

## Status

✅ Stage 1: Self-Report GUI (Complete)
⏳ Stage 2: Exploration Engine (In Progress)
⏳ Stage 3: Rater Agent (In Progress)
⏳ Stage 4: PDF Reporter (Not Started)

## License

Clinical use requires supervision by a licensed mental health professional.
