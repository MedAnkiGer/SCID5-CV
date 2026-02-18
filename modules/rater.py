"""Stage 3: Rater Agent — Claude API scoring of patient transcripts.

Sends each transcript + criterion description to Claude for clinical scoring.
Returns structured JSON with score, rationale, confidence, and unresolved flag.
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "rater_system_prompt.txt"

# Configurable model — use sonnet for cost-effectiveness, opus for highest accuracy
DEFAULT_MODEL = "claude-sonnet-4-6"


def _load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _build_user_message(transcript: str, criterion: dict, language: str) -> str:
    """Build the user message for the Claude API call."""
    lang_suffix = "_de" if language == "de" else "_en"
    criterion_desc = criterion.get(f"description{lang_suffix}", criterion.get("description_en", ""))
    followup_q = criterion.get(f"followup_question{lang_suffix}", "")

    return f"""## Criterion
{criterion_desc}

## Interview Question Asked
{followup_q}

## Patient Transcript
{transcript}

## Instructions
Rate this criterion based on the transcript above. Respond with JSON only."""


def evaluate_response(
    transcript: str,
    criterion: dict,
    language: str = "de",
    model: str = DEFAULT_MODEL,
) -> dict:
    """Call Claude API to evaluate a patient's response against a criterion.

    Args:
        transcript: The patient's transcribed response.
        criterion: Criterion dict from questions.json (includes description, followup, etc.).
        language: 'de' or 'en'.
        model: Claude model to use.

    Returns:
        dict with keys: score ("?"|0|1|2), rationale (str), confidence (float),
        unresolved (bool), clarifying_question (str|None).
        Score "?" means inadequate information — exploration was inconclusive.
    """
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system_prompt = _load_system_prompt()
    user_message = _build_user_message(transcript, criterion, language)

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()

    # Parse JSON from response — handle potential markdown code fences
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # Remove first and last lines (code fences)
        json_lines = []
        inside = False
        for line in lines:
            if line.strip().startswith("```") and not inside:
                inside = True
                continue
            elif line.strip().startswith("```") and inside:
                break
            elif inside:
                json_lines.append(line)
        raw_text = "\n".join(json_lines)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: try to extract JSON from the response
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(raw_text[start:end])
        else:
            result = {
                "score": 1,
                "rationale": f"Failed to parse API response: {raw_text[:200]}",
                "confidence": 0.0,
                "unresolved": True,
                "clarifying_question": "Could you elaborate on your experience?",
            }

    # Validate and normalize the result
    result.setdefault("score", "?")
    result.setdefault("rationale", "")
    result.setdefault("confidence", 0.5)
    result.setdefault("unresolved", False)
    result.setdefault("clarifying_question", None)

    # Normalize score: "?" stays as-is, numeric scores clamped to 0-2
    score = result["score"]
    if score == "?" or score == "?":
        result["score"] = "?"
        result["unresolved"] = True  # "?" always means unresolved
    else:
        try:
            result["score"] = max(0, min(2, int(score)))
        except (ValueError, TypeError):
            result["score"] = "?"
            result["unresolved"] = True

    result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

    return result


def evaluate_with_clarification(
    original_transcript: str,
    clarification_transcript: str,
    criterion: dict,
    language: str = "de",
    model: str = DEFAULT_MODEL,
) -> dict:
    """Re-evaluate with both original and clarification transcripts combined."""
    combined = (
        f"[Original response]\n{original_transcript}\n\n"
        f"[Clarification response]\n{clarification_transcript}"
    )
    return evaluate_response(combined, criterion, language, model)
