# Open Questions

Known gaps and unresolved decisions in the pipeline. Each entry says what the code does
today, so nothing here is a silent failure — but each one needs a decision or a data fix
before the instrument can be called complete.

Status: `OPEN` needs a decision · `BLOCKED` needs the source booklet · `WATCH` handled for
now, revisit.

---

## 1. PTSD Criterion D is missing three of its seven symptoms — `BLOCKED`

**G23, G24 and G25 were never extracted into `data/questions.json`.**

DSM-5 Criterion D (negative alterations in cognition and mood associated with the traumatic
event) has seven symptoms; the question bank carries only four:

| in the bank | missing |
|---|---|
| G26 Persistent negative emotional state | G23 |
| G27 Markedly diminished interest or participation | G24 |
| G28 Feelings of detachment or estrangement | G25 |
| G29 Persistent inability to experience positive emotions | |

The criterion text at G30 still cites the full range — *"AT LEAST TWO OF THE ABOVE CRITERION
D SXS (G23–G29) ARE RATED '+'"* — so the aggregate is counting over a range that only
partly exists.

**What the pipeline does today:** `data/derived_rules.json` records the gap explicitly
(`"missing_from_questions": ["G23", "G24", "G25"]`) and counts the four items that exist.
The note is carried into the derived rating's rationale, so it appears in the report.

**Why it matters:** a patient who meets Criterion D on two of the three missing symptoms
alone will be scored `-` here and cannot be diagnosed with PTSD by this pipeline. This is a
false-negative path, not a cosmetic gap.

**Needed:** the three questions from the SCID-5-CV booklet (likely: inability to remember an
important aspect of the event; persistent exaggerated negative beliefs about oneself, others
or the world; persistent distorted cognitions about the cause or consequences leading to
self-blame or blame of others). Add them to Module G in order, then extend the `of` list in
the `G30` rule and delete `missing_from_questions`.

---

## 2. Other questions missing from the extraction — `BLOCKED`

Same cause as above, found while auditing the branching. All are gaps in the id sequence:

| ids | what they should be | consequence |
|---|---|---|
| **B21, B22** | Grossly disorganized or catatonic behavior | `C2` and `C19` (Schizophrenia / Brief Psychotic Disorder Criterion A) both cite `[B21–B22]` as one of the five symptom classes. That route to Criterion A cannot currently be assessed |
| **G11, G12** | Between "Past Lifetime Event #1" (G10) and "Criterion A - Exposure" (G13) — probably further trauma events | Trauma history may be under-recorded |
| **E36** | Last item of Module E (the substance-use threshold's continuation) | `E35`'s skip says *"Continue with E36, next page"*; E35 is the last question in the module, so the skip now falls through to `F1`, which is where the interview should go anyway. Harmless today, but the item's content is absent |

**Needed:** re-extract these from the booklet, or confirm they are intentionally out of scope.

---

## 3. Nineteen skip targets have no recorded destination — `OPEN`

`tools/fix_skip_targets.py` resolved 70 broken skip targets to real question ids. Nineteen
could not be resolved because the extraction recorded a placeholder (`PRIMARY`,
`C8_NO_PATH`, `D17_diagnosis_partial_or_full_remission`) or a diagnosis to write down rather
than a question to go to.

These are now the explicit sentinel `CONTINUE` (carry on in order), with the original
wording preserved in each question's `skip_if_note`:

`A12` (both branches), `A53`, `A77`, `A89`, `C6`, `C8`, `C12`, `C17`, `C24`, `D17`, `D18`,
`D19`, `D20`, `D21`, `E35`, `F20`, `F39`, `G7`

**What the pipeline does today:** exactly what it did before — the next question in order.
The difference is that this is now stated on purpose rather than being an accident of an
unmatched target.

**Needed:** check each against the booklet. Most are "record this diagnosis, then continue",
in which case `CONTINUE` is right and the only thing missing is somewhere for the clinician
to record the diagnosis. The `PRIMARY` ones may direct elsewhere.

---

## 4. Clinical-judgement items still go to the rater with no transcript — `OPEN`

Forty-five clinical questions put nothing to the patient. Seventeen of them only count other
ratings and are now computed (`modules/aggregator.py`). The remaining **twenty-eight** are
judgement calls over accumulated evidence:

- etiology rule-outs ("not attributable to the physiological effects of a substance")
- differential exclusions ("not better explained by another mental disorder")
- the Module C and D diagnostic logic (`C2`, `C4`, `C9`, `C13`–`C15`, `C18`–`C20`, `C22`,
  `D1`–`D6`, `D8`, `D11`–`D14`, …)
- two observer ratings the interviewer makes from the interview itself: `B20` (disorganized
  speech) and `B24` (diminished emotional expressiveness)

**What the pipeline does today:** they reach the rater with a marker string instead of a
transcript, so they come back `?` and unresolved. They are visible in the report as
unresolved, not silently skipped.

**Needed:** a decision on each family. Options: (a) give the rater the accumulated evidence
so far as context rather than a marker; (b) leave them for the clinician and add an
explicit "clinician decides" state to the UI; (c) for `B20`/`B24`, rate from the whole
interview transcript rather than a single answer.

---

## 5. Mood quality is inferred from the transcript, not confirmed — `WATCH`

DSM-5 requires four Criterion B symptoms rather than three when a manic or hypomanic
episode's mood was **only irritable** and never elevated. The pipeline now records this: the
rater returns `mood_quality` (`elevated` / `irritable_only` / `unclear`) on `A29`, `A41` and
`A54`, it is stored on the response, and `A38`, `A49` and `A63` take their threshold from it.
When it is `unclear`, the lower threshold applies and any count that would change under the
higher one is flagged unresolved.

**Open:** the value comes from the rater reading the patient's answer, not from the
clinician. For a decision that moves a diagnostic threshold, it may need to be clinician-
confirmed in the UI rather than inferred. Also, an episode's mood quality is currently
recorded once per screening question; a patient with several past episodes of different
quality is not represented.

---

## 6. One question had no id — `WATCH`

The Module A question "Additional past manic episodes screening" (between `A65` and `A66`)
came through the extraction with `id: null`, which put a `None` into the question order.
`tools/fix_skip_targets.py` assigned it **`A65b`**.

**Needed:** confirm against the booklet that this is a supplementary probe after A65 rather
than a numbered item whose id was dropped. If it has a real number, renumber it.

---

## 7. Timeframe qualifiers are not represented in session state — `OPEN`

Several criteria are scoped to a period the data model does not carry. `G41` (PTSD
diagnostic summary) requires its four criteria to be rated `+` **for the past month**;
Module A distinguishes current from past episodes by which block of questions is asked, not
by a field on the rating. The aggregates therefore count ratings without checking the
timeframe, and the rule file notes this where it applies.

**Needed:** decide whether a `timeframe` field on each response is worth adding, or whether
the block structure is a sufficient proxy.
