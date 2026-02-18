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

## Clinical Guardrails

- **DSM-5 Fidelity**: Ratings align with diagnostic criteria
- **Confidence Tracking**: LLM confidence < 0.7 triggers clarification loop
- **Privacy**: No raw audio logged; anonymized transcripts only
- **Bilingual Support**: German and English via Whisper API

## Status

✅ Stage 1: Self-Report GUI (Complete)
⏳ Stage 2: Exploration Engine (In Progress)
⏳ Stage 3: Rater Agent (In Progress)
⏳ Stage 4: PDF Reporter (Not Started)

## License

Clinical use requires supervision by a licensed mental health professional.
