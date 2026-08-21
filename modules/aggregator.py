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

# Questions that establish whether an episode's mood was elevated/expansive or
# only irritable. DSM-5 requires one more Criterion B symptom in the
# irritable-only case, so the rater records `mood_quality` on these and the
# aggregates below read it. `modules/rater.py` imports this set to know when to
# ask for the extra field.
MOOD_QUALITY_QUESTIONS = {
    rule["mood_quality_from"] for rule in RULES.values()
    if rule.get("mood_quality_from")
}

MOOD_QUALITY_VALUES = ("elevated", "irritable_only", "unclear")


def is_derived(qid: str) -> bool:
    return qid in RULES


def _threshold(rule: dict, responses: dict) -> tuple[int, str]:
    """Symptom threshold for a rule, and how the mood quality was established.

    DSM-5 asks for one more Criterion B symptom when the mood was irritable but
    not elevated. Returns (threshold, note) where the note is empty unless the
    mood quality changes or qualifies the count.
    """
    base = rule["n"]
    higher = rule.get("n_if_irritable_only")
    source_qid = rule.get("mood_quality_from")
    if not higher or not source_qid:
        return base, ""

    quality = (responses.get(source_qid) or {}).get("mood_quality")
    if quality == "irritable_only":
        return higher, (
            f" The mood at {source_qid} was recorded as irritable only, not "
            f"elevated, so DSM-5 requires {higher} symptoms rather than {base}."
        )
    if quality == "elevated":
        return base, (
            f" The mood at {source_qid} was recorded as elevated or expansive, "
            f"so the {base}-symptom threshold applies."
        )
    return base, (
        f" The quality of the mood was not established at {source_qid}, so the "
        f"{base}-symptom threshold is applied; {higher} would be required if the "
        f"mood was only irritable."
    )


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

    needed, mood_note = _threshold(rule, responses)
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

    rationale += mood_note

    # If the mood quality was never established, a count that sits between the
    # two thresholds is only correct on the assumption that the mood was
    # elevated. Flag it rather than let the assumption pass silently.
    higher = rule.get("n_if_irritable_only")
    source_qid = rule.get("mood_quality_from")
    if higher and source_qid and score == "+" and len(met) < higher:
        quality = (responses.get(source_qid) or {}).get("mood_quality")
        if quality != "elevated":
            unresolved = True
            rationale += (
                f" Only {len(met)} symptoms are met, so this rating holds only "
                f"if the mood was elevated or expansive. Confirm the quality of "
                f"the mood at {source_qid} before accepting it."
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
            "mood_quality_from": rule.get("mood_quality_from"),
            "mood_quality": (
                (responses.get(rule["mood_quality_from"]) or {}).get("mood_quality")
                if rule.get("mood_quality_from") else None
            ),
            "met": met,
            "absent": absent,
            "unsettled": unsettled,
            "missing_from_questions": rule.get("missing_from_questions", []),
        },
    }
