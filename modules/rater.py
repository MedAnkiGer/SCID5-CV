"""Rater Agent — Claude API scoring of patient transcripts against SCID-5-CV criteria.

Sends each transcript + criterion to Claude for clinical scoring.
Returns structured JSON with score (+/-/?), rationale, confidence, unresolved flag.
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from modules.aggregator import MOOD_QUALITY_QUESTIONS, MOOD_QUALITY_VALUES

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "rater_system_prompt.txt"

# Configurable model — use sonnet for cost-effectiveness, opus for highest accuracy
DEFAULT_MODEL = "claude-sonnet-4-6"


def _load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


MOOD_QUALITY_INSTRUCTION = """

## Additional field required for this question
This question establishes the QUALITY of the mood during the episode, which sets the symptom \
threshold for the rest of this section: DSM-5 requires three Criterion B symptoms if the mood \
was elevated or expansive, but four if it was ONLY irritable. The rating cannot be completed \
later without this, so record it now. Add one further field to your JSON:

  "mood_quality": "elevated" | "irritable_only" | "unclear"

- "elevated" — the patient describes elevated, expansive, euphoric, "high" or "on top of the \
world" mood at any point in the episode, whether or not irritability was present as well.
- "irritable_only" — the patient describes ONLY irritability, anger or short temper, with no \
elevated or expansive mood at any point.
- "unclear" — the transcript does not establish which. Do not guess, and do not infer elevated \
mood from energy or activity alone.

Base this only on what the patient actually said. This field is independent of the score: a \
criterion rated "-" can still have a recorded mood quality, and one rated "+" can be "unclear"."""


def _build_user_message(transcript: str, criterion: dict, language: str) -> str:
    """Build the user message for the Claude API call."""
    lang_suffix = "_de" if language == "de" else "_en"

    # Criterion description — prefer localised, fall back to English
    criterion_desc = (
        criterion.get(f"criterion_description{lang_suffix}")
        or criterion.get("criterion_description", "")
    )

    # The question text that was read to the patient
    interviewer_text = (
        criterion.get(f"interviewer_text{lang_suffix}")
        or criterion.get("interviewer_text", "")
    )

    criterion_label = criterion.get("criterion_label", criterion.get("id", ""))

    extra = (
        MOOD_QUALITY_INSTRUCTION
        if criterion.get("id") in MOOD_QUALITY_QUESTIONS else ""
    )

    return f"""## Criterion Being Rated
{criterion_label}

## DSM-5 Criterion (exact wording)
{criterion_desc}

## Question Asked to Patient
{interviewer_text}

## Patient's Transcript
{transcript}
{extra}

Rate whether this criterion is met (+), not met (-), or unclear (?). Respond with JSON only."""


def _parse_rating(raw_text: str) -> dict | None:
    """Pull the rating object out of a model response, or None if it is not there.

    Handles markdown code fences and trailing prose. Returns None rather than
    raising: the caller retries once and then degrades to an unresolved rating,
    because a parse failure used to abort the interview mid-module.
    """
    text = raw_text.strip()

    if text.startswith("```"):
        lines, inside, json_lines = text.split("\n"), False, []
        for line in lines:
            if line.strip().startswith("```") and not inside:
                inside = True
            elif line.strip().startswith("```") and inside:
                break
            elif inside:
                json_lines.append(line)
        text = "\n".join(json_lines)

    for candidate in (text, text[text.find("{"):text.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


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
        dict with keys: score ("+"|"-"|"?"), rationale (str), confidence (float),
        unresolved (bool), clarifying_question (str|None).
        Score "?" means inadequate information — a clarifying question is needed.
    """
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system_prompt = _load_system_prompt()
    user_message = _build_user_message(transcript, criterion, language)

    def ask(message: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )
        return response.content[0].text.strip()

    raw_text = ask(user_message)
    result = _parse_rating(raw_text)

    if result is None:
        # A rationale quoting the patient can come back with an unescaped quote.
        # One retry saying so is cheaper than losing the question — and losing it
        # used to abort the whole interview.
        raw_text = ask(
            user_message
            + "\n\nIMPORTANT: your previous response was not valid JSON. Return a "
              "single JSON object and nothing else. Escape any double quote that "
              "appears inside a string value, or paraphrase instead of quoting."
        )
        result = _parse_rating(raw_text)

    if result is None:
        result = {
            "score": "?",
            "rationale": f"Rater returned unparseable output: {raw_text[:300]}",
            "confidence": 0.0,
            "unresolved": True,
            "clarifying_question": "Could you tell me a bit more about that?",
        }

    # Validate and normalize the result
    result.setdefault("score", "?")
    result.setdefault("rationale", "")
    result.setdefault("confidence", 0.5)
    result.setdefault("unresolved", False)
    result.setdefault("clarifying_question", None)

    # Normalize score to "+", "-", or "?"
    # Do NOT override Claude's unresolved flag for + and - scores:
    # Claude may give a tentative +/- with low confidence and still flag
    # that a clarifying question is needed (e.g. duration threshold unclear).
    score = str(result.get("score", "?")).strip()
    if score in ("+", "YES", "yes", "1", "true", "True"):
        result["score"] = "+"
    elif score in ("-", "NO", "no", "0", "false", "False"):
        result["score"] = "-"
    else:
        result["score"] = "?"
        result["unresolved"] = True  # ? always means unresolved

    result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

    # Mood-quality questions carry an extra field: whether the episode's mood was
    # elevated/expansive or only irritable. The Criterion B symptom threshold
    # downstream depends on it (see modules/aggregator.py), so it is recorded on
    # the response rather than re-derived later. Anything unrecognised, or a
    # missing field on a question that should have one, becomes "unclear" —
    # which keeps the lower threshold but flags the aggregate for review.
    if criterion.get("id") in MOOD_QUALITY_QUESTIONS:
        quality = str(result.get("mood_quality", "")).strip().lower().replace(" ", "_")
        if quality in ("irritable", "irritable_only", "only_irritable"):
            quality = "irritable_only"
        elif quality in ("elevated", "expansive", "elevated_or_expansive", "euphoric"):
            quality = "elevated"
        result["mood_quality"] = quality if quality in MOOD_QUALITY_VALUES else "unclear"
    else:
        result.pop("mood_quality", None)

    return result


def evaluate_with_clarification(
    original_transcript: str,
    clarification_transcript: str,
    criterion: dict,
    language: str = "de",
    model: str = DEFAULT_MODEL,
) -> dict:
    """Re-evaluate with original + one clarification transcript combined.

    For multi-round clarification, build the full context string externally
    using _build_full_context() in main.py and call evaluate_response() directly.
    """
    combined = (
        f"[Initial answer]\n{original_transcript}\n\n"
        f"[Follow-up answer 1]\n{clarification_transcript}"
    )
    return evaluate_response(combined, criterion, language, model)
