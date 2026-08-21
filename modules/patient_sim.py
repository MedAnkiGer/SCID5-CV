"""Patient Agent — a simulated interviewee for in-silico runs of the pipeline.

This is the mirror image of `modules/rater.py`. The rater reads a transcript and
scores it; the patient *produces* the transcript. Together they let the whole
interview be run end to end with no human and no microphone.

Design rule: the simulated patient only ever sees what a real patient would see
— the interviewer's spoken question. It is never shown `criterion_description`,
`notes`, `rating_options`, or the rater's reasoning. Leaking those would make
the test circular: the patient would answer the criterion rather than the
question, and the rater would look better than it is.

Ground truth lives in a persona file (`data/personas/*.json`). The persona says
what is true of the person; `expected_scores` in the same file says what the
rater ought to conclude, which is what makes a run scoreable.
"""

import json
import os
from pathlib import Path

import anthropic
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
PERSONAS_DIR = ROOT / "data" / "personas"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "patient_system_prompt.txt"

DEFAULT_MODEL = "claude-opus-5"

# How many previous question/answer pairs the patient carries verbatim. The
# persona (in the system prompt) is always present, so this only governs how far
# back verbatim self-consistency reaches. SCID modules are topically grouped, so
# a recency window keeps related answers coherent without resending 378 turns.
HISTORY_EXCHANGES = 14

# Server-side refusal fallback (beta). A simulated patient reporting suicidal
# ideation is exactly the sort of content a safety classifier may decline; with
# fallbacks on, the API re-runs the turn on another model inside the same call
# instead of returning nothing. The installed SDK has no typed `fallbacks`
# parameter, so it goes through extra_body; if the account or SDK rejects it we
# turn it off for the rest of the process and carry on unprotected.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_use_server_fallbacks = True

# What the patient says when a turn is refused outright. Recorded verbatim in
# the run log and flagged, so a refusal never looks like a real "?" answer.
REFUSAL_PLACEHOLDER = "[patient model declined to answer this question]"


def _load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

def list_personas() -> list[dict]:
    """Return every persona in data/personas, sorted by id."""
    if not PERSONAS_DIR.exists():
        return []
    out = []
    for path in sorted(PERSONAS_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            p = json.load(f)
        p["_path"] = str(path)
        out.append(p)
    return out


def load_persona(ref: str) -> dict:
    """Load a persona by id ('mdd_moderate') or by path to a .json file."""
    path = Path(ref)
    if not path.exists():
        path = PERSONAS_DIR / (ref if ref.endswith(".json") else f"{ref}.json")
    if not path.exists():
        known = ", ".join(p["id"] for p in list_personas()) or "none found"
        raise FileNotFoundError(f"No persona {ref!r}. Available: {known}")
    with open(path, encoding="utf-8") as f:
        persona = json.load(f)
    persona["_path"] = str(path)
    return persona


def _render_case_file(persona: dict) -> str:
    """Turn a persona dict into the prose case file the patient model reads."""
    lines = ["## CASE FILE — this is who you are", ""]

    demo = persona.get("demographics") or {}
    if demo:
        lines.append("### Who you are")
        for key, value in demo.items():
            lines.append(f"- {key.replace('_', ' ').capitalize()}: {value}")
        lines.append("")

    if persona.get("presenting_problem"):
        lines += ["### Why you are here", persona["presenting_problem"], ""]

    if persona.get("background"):
        lines += ["### Your background", persona["background"], ""]

    style = persona.get("style") or {}
    if style:
        lines.append("### How you talk")
        for key, value in style.items():
            lines.append(f"- {key.replace('_', ' ').capitalize()}: {value}")
        lines.append("")

    facts = persona.get("clinical_facts")
    if facts:
        lines.append("### What is true of you right now")
        lines.append(
            "These are your actual experiences. Report them when asked about "
            "them, in your own words, with the durations and frequencies given."
        )
        lines.append("")
        if isinstance(facts, dict):
            for topic, items in facts.items():
                lines.append(f"**{topic}**")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")
        else:
            for item in facts:
                lines.append(f"- {item}")
            lines.append("")

    absent = persona.get("absent")
    if absent:
        lines.append("### What is NOT true of you")
        lines.append(
            "If you are asked about any of these, say no — clearly and without "
            "hedging. Do not soften a no into a maybe."
        )
        lines.append("")
        for item in absent:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

class SimulatedPatient:
    """A persona-driven interviewee that answers one question at a time.

    Usage:
        patient = SimulatedPatient(load_persona("mdd_moderate"))
        patient.answer("How old are you?")
        patient.answer("How long has that lasted?", follow_up=True)

    The instance is stateful: every question and answer is kept, so answers
    stay consistent with each other across the interview.
    """

    def __init__(
        self,
        persona: dict,
        language: str = "en",
        model: str = DEFAULT_MODEL,
        effort: str = "low",
        max_tokens: int = 2000,
    ):
        self.persona = persona
        self.language = language
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        lang_name = "German" if language == "de" else "English"
        self.system_prompt = (
            f"{_load_system_prompt()}\n\n"
            f"Speak {lang_name}.\n\n"
            f"{_render_case_file(persona)}"
        )

        self.history: list[dict] = []   # rolling window sent to the API
        self.log: list[dict] = []       # every exchange, for the run record
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.refusals = 0

    # -- internals ---------------------------------------------------------

    def _create(self, messages: list[dict]):
        """One API call, with refusal fallbacks when the account supports them."""
        global _use_server_fallbacks
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            # Effort, not temperature: sampling parameters are rejected on
            # current models. "low" keeps a short spoken answer cheap.
            output_config={"effort": self.effort},
            system=[{
                "type": "text",
                "text": self.system_prompt,
                # The system prompt is byte-identical for the whole interview,
                # so it is a good cache prefix across ~400 calls.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
        )
        if _use_server_fallbacks:
            try:
                return self.client.beta.messages.create(
                    betas=[FALLBACK_BETA],
                    extra_body={"fallbacks": "default"},
                    **kwargs,
                )
            except (anthropic.BadRequestError, anthropic.NotFoundError,
                    anthropic.PermissionDeniedError, TypeError):
                _use_server_fallbacks = False
        return self.client.messages.create(**kwargs)

    def _record_usage(self, response) -> None:
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.input_tokens += (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        self.output_tokens += usage.output_tokens or 0

    # -- public ------------------------------------------------------------

    def answer(
        self,
        question_text: str,
        question_id: str | None = None,
        follow_up: bool = False,
    ) -> str:
        """Answer one interviewer question and remember having done so.

        Args:
            question_text: exactly what the clinician says out loud. Nothing
                clinician-only (criterion text, notes, rating options) belongs
                here — see the module docstring.
            question_id: SCID question id, recorded in the log only.
            follow_up: True when this is a clarifying question about the answer
                just given, rather than a new interview question.

        Returns:
            The patient's spoken answer.
        """
        prompt = question_text.strip()
        if follow_up:
            prompt = f"(follow-up on your last answer) {prompt}"

        messages = self.history[-(HISTORY_EXCHANGES * 2):] + [
            {"role": "user", "content": prompt}
        ]
        response = self._create(messages)
        self._record_usage(response)

        if getattr(response, "stop_reason", None) == "refusal":
            self.refusals += 1
            text = REFUSAL_PLACEHOLDER
        else:
            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            ).strip()
            if not text:
                text = REFUSAL_PLACEHOLDER
                self.refusals += 1

        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": text})
        self.log.append({
            "question_id": question_id,
            "follow_up": follow_up,
            "question": question_text,
            "answer": text,
            "refused": text == REFUSAL_PLACEHOLDER,
        })
        return text

    def usage_summary(self) -> dict:
        return {
            "model": self.model,
            "effort": self.effort,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "refusals": self.refusals,
        }
