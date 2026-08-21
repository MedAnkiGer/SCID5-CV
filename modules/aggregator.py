"""Aggregator — scores the SCID-5-CV items that only count other items.

Seventeen questions in the interview put nothing to the patient. They say things
like "AT LEAST FIVE OF THE ABOVE CRITERION A SXS (A1-A9) ARE RATED '+'" — the
answer is already in the session, it just needs counting. Sending them to the
rater is meaningless: there is no transcript, so it can only return "?".

The rules live in `data/derived_rules.json`, each quoting the criterion text it
encodes so the arithmetic can be checked against the booklet. This module does
the counting; `app.py` applies it as the interview walks past each such item, so
neither the clinician nor the simulated patient is ever asked one.

Not covered here — deliberately: the clinical-judgement items (etiology
rule-outs, "not better explained by another mental disorder", the Module C/D
differential). Those weigh evidence rather than count criteria, and guessing
them from stored scores would be inventing a diagnosis.
"""

import json
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "derived_rules.json"


def load_rules() -> dict:
    """Rule table keyed by question id (comment keys stripped)."""
    with open(RULES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


RULES = load_rules()


def is_derived(qid: str) -> bool:
    return qid in RULES


def derive_rating(qid: str, responses: dict) -> dict | None:
    """Score an aggregate item from the ratings it counts.

    Args:
        qid: the aggregate question's id.
        responses: `session["interview_responses"]`.

    Returns:
        A rating dict shaped like the rater's — score, rationale, confidence,
        unresolved, clarifying_question — plus a `summary` line to store as the
        item's transcript and a `derived` block recording the arithmetic. None
        if this question is not an aggregate.
    """
    rule = RULES.get(qid)
    if rule is None:
        return None

    needed = rule["n"]
    members = rule["of"]

    met, absent, unsettled = [], [], []
    for member in members:
        score = (responses.get(member) or {}).get("score")
        if score == "+":
            met.append(member)
        elif score == "-":
            absent.append(member)
        else:
            # Never rated (branch skipped it, or the interview stopped early),
            # or rated "?" — either way it is not settled.
            unsettled.append(member)

    if len(met) >= needed:
        score = "+"
        unresolved = False
    elif len(met) + len(unsettled) < needed:
        score = "-"
        unresolved = False
    else:
        # The unsettled items could still carry it over the threshold.
        score = "?"
        unresolved = True

    counted = f"{len(met)} of {len(members)} rated '+' (threshold {needed})"
    rationale = f"{rule['label']}: {counted}."
    if met:
        rationale += f" Met: {', '.join(met)}."
    if absent:
        rationale += f" Not met: {', '.join(absent)}."
    if unsettled:
        rationale += (
            f" Not settled: {', '.join(unsettled)} — "
            f"{'unrated or rated' if score == '?' else 'rated'} '?'."
        )

    # DSM-5 raises the threshold by one when the mood was irritable but not
    # elevated. Nothing in session state says which it was, so a count that
    # clears the lower threshold only is scored '+' and flagged for review.
    irritable_n = rule.get("n_if_irritable_only")
    if score == "+" and irritable_n and len(met) < irritable_n:
        unresolved = True
        rationale += (
            f" Note: {irritable_n} symptoms are required if the mood was only "
            f"irritable rather than elevated, and only {len(met)} are met. "
            f"Confirm the quality of the mood before accepting this rating."
        )

    if rule.get("note"):
        rationale += f" {rule['note']}"

    return {
        "score": score,
        "rationale": rationale,
        "confidence": 0.5 if unresolved else 1.0,
        "unresolved": unresolved,
        "clarifying_question": None,
        "summary": f"[computed] {counted}"
                   + (f"; met: {', '.join(met)}" if met else ""),
        "derived": {
            "rule": qid,
            "source": rule["source"],
            "threshold": needed,
            "met": met,
            "absent": absent,
            "unsettled": unsettled,
            "missing_from_questions": rule.get("missing_from_questions", []),
        },
    }
