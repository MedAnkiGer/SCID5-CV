"""Generate synthetic patient answers with known ground-truth scores.

For each criterion in questions.json, uses Claude to produce a realistic
patient transcript for each score level (?, 0, 1, 2). The output can be
fed into the evaluation pipeline to measure rater accuracy.

Usage:
    python tools/generate_test_answers.py                  # all criteria, all scores
    python tools/generate_test_answers.py --criteria PPD_1 BPD_2
    python tools/generate_test_answers.py --scores 0 2     # only absent & threshold
    python tools/generate_test_answers.py --language en
    python tools/generate_test_answers.py --variants 3     # 3 answers per score level
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = ROOT / "data" / "questions.json"
OUTPUT_DIR = ROOT / "data" / "test_answers"

DEFAULT_MODEL = "claude-sonnet-4-6"

SCORE_DESCRIPTIONS = {
    "?": (
        "INADEQUATE / VAGUE: The patient gives an evasive, off-topic, or overly "
        "brief response that does NOT provide enough information to determine "
        "whether the criterion is met. The answer should be frustratingly vague — "
        "maybe a one-liner, a deflection, or a tangent that doesn't address the "
        "actual question."
    ),
    "0": (
        "ABSENT (score 0): The patient clearly does NOT meet this criterion. "
        "They may acknowledge the topic but describe experiences firmly within "
        "normal limits. They provide concrete examples that show the behavior/ "
        "experience is not present or is ordinary and situation-appropriate."
    ),
    "1": (
        "SUB-THRESHOLD (score 1): The patient shows SOME evidence of the "
        "criterion but does not fully meet the DSM-5 threshold. The behavior "
        "exists but is not pervasive, persistent, or severe enough. Maybe it "
        "happens occasionally, in limited contexts, or with mild impact."
    ),
    "2": (
        "THRESHOLD (score 2): The patient CLEARLY meets this criterion per "
        "DSM-5 standards. The behavior/experience is pervasive (across "
        "situations), persistent (ongoing pattern since adolescence/early "
        "adulthood), and causes significant distress or functional impairment. "
        "They give vivid, concrete examples."
    ),
}

SYSTEM_PROMPT = """\
You are a clinical simulation expert. Your task is to generate realistic \
patient interview responses for SCID-5-PD diagnostic criteria.

Rules:
- Write in FIRST PERSON as the patient speaking naturally during a clinical interview.
- The response should sound like real spoken language — informal, with natural \
  hesitations, filler words, and imperfect grammar where appropriate.
- Do NOT use clinical terminology the patient wouldn't know.
- Vary the length and style: some patients are talkative, others terse.
- Include concrete personal examples, anecdotes, or situations when appropriate.
- The response should be 3-8 sentences for scores 0/1/2, and 1-3 sentences for "?".
- Write in the language specified.
- Return ONLY the patient's spoken response, nothing else. No quotation marks, \
  no labels, no metadata."""


def load_questions() -> dict:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_answer(
    client: Anthropic,
    criterion_id: str,
    criterion: dict,
    score: str,
    language: str,
    model: str,
    variant: int = 1,
) -> str:
    """Generate one synthetic patient answer for a criterion at a given score level."""
    lang_suffix = f"_{language}"
    description = criterion.get(f"description{lang_suffix}", criterion.get("description_en"))
    followup = criterion.get(f"followup_question{lang_suffix}", criterion.get("followup_question_en"))

    lang_name = "German" if language == "de" else "English"
    score_instruction = SCORE_DESCRIPTIONS[score]

    user_msg = f"""\
Generate a realistic patient response for this SCID-5-PD interview scenario.

## Criterion being assessed
ID: {criterion_id}
Description: {description}

## Interview question asked by clinician
{followup}

## Target score level
{score_instruction}

## Language
Respond in {lang_name}.

## Variant
This is variant #{variant}. Make it distinct from other variants — vary the \
patient's personality, background, specific examples, and speaking style."""

    response = client.messages.create(
        model=model,
        max_tokens=512,
        temperature=0.9,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text.strip()


def main():
    parser = argparse.ArgumentParser(description="Generate test answers with ground-truth scores")
    parser.add_argument("--criteria", nargs="*", help="Specific criterion IDs (e.g. PPD_1 BPD_2)")
    parser.add_argument("--scores", nargs="*", default=["?", "0", "1", "2"],
                        help="Score levels to generate (default: all)")
    parser.add_argument("--language", default="de", choices=["de", "en"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--variants", type=int, default=1,
                        help="Number of answer variants per score level (default: 1)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (default: data/test_answers/TIMESTAMP.json)")
    args = parser.parse_args()

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    questions = load_questions()

    # Collect all criteria
    all_criteria = {}
    for disorder_id, disorder in questions["disorders"].items():
        for crit_id, crit in disorder["criteria"].items():
            crit_with_context = {**crit, "disorder": disorder_id}
            all_criteria[crit_id] = crit_with_context

    # Filter if requested
    if args.criteria:
        all_criteria = {k: v for k, v in all_criteria.items() if k in args.criteria}
        missing = set(args.criteria) - set(all_criteria.keys())
        if missing:
            print(f"Warning: criteria not found: {missing}")

    total = len(all_criteria) * len(args.scores) * args.variants
    print(f"Generating {total} answers ({len(all_criteria)} criteria x "
          f"{len(args.scores)} scores x {args.variants} variants)")
    print(f"Model: {args.model} | Language: {args.language}")
    print()

    results = []
    done = 0

    for crit_id, crit in sorted(all_criteria.items()):
        for score in args.scores:
            for variant in range(1, args.variants + 1):
                done += 1
                label = f"[{done}/{total}] {crit_id} score={score}"
                if args.variants > 1:
                    label += f" v{variant}"
                print(f"  {label} ... ", end="", flush=True)

                try:
                    answer = generate_answer(
                        client, crit_id, crit, score, args.language, args.model, variant
                    )
                    results.append({
                        "criterion_id": crit_id,
                        "disorder": crit["disorder"],
                        "ground_truth_score": "?" if score == "?" else int(score),
                        "language": args.language,
                        "variant": variant,
                        "transcript": answer,
                    })
                    print("OK")
                except Exception as e:
                    print(f"FAILED: {e}")
                    results.append({
                        "criterion_id": crit_id,
                        "disorder": crit["disorder"],
                        "ground_truth_score": "?" if score == "?" else int(score),
                        "language": args.language,
                        "variant": variant,
                        "transcript": None,
                        "error": str(e),
                    })

    # Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"test_answers_{timestamp}.json"

    output = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "model": args.model,
            "language": args.language,
            "variants_per_score": args.variants,
            "scores": args.scores,
            "total_items": len(results),
            "source_questions_version": questions["metadata"]["version"],
        },
        "answers": results,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    succeeded = sum(1 for r in results if r.get("transcript") is not None)
    print(f"\nDone: {succeeded}/{len(results)} generated successfully")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
