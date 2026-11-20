# Phase 23 — Report Quality Rubric (human-rated)

**Status:** designed and unpopulated. No human scores have been collected yet, so
this document contains **no quality claim**. Phase 22 numbers describe *validity*
(schema, citations, evidence support); they do not describe *usefulness*, and the
gap between the two is what this rubric exists to close.

**Why not automate it.** The automated report evaluator scores facts that can be
checked mechanically against a fixture: is the JSON valid, is every citation id
real, is every hypothesis backed by a retrieved chunk, does the outcome match the
label. Judgements like "is this root cause plausible" or "would an on-call
engineer act on this" are exactly the judgements a keyword or LLM-as-judge
routine is least reliable at, and the failure mode of an automated judge is
silent agreement with the generator. So quality is rated by a human, on a fixed
scale, against a fixed protocol, with disagreement measured.

---

## 1. How to run a rating session

1. Pick 10–15 reports produced by the **same** system version and mode.
   Record the mode (`offline` / `local` / `live`), model name, dataset version and
   git commit in the session header. Do not mix modes in one session.
2. Generate a blind packet: report text, the incident input, and the retrieved
   evidence chunks. Do **not** include the generator's own `valid`,
   `citation_valid`, `evidence_status` or confidence fields — they anchor the
   rater toward agreement with the system.
3. Use **two raters minimum**, independently, without conferring.
4. Score each dimension 0–4 using the anchors in §3. Score what is on the page,
   not what the system probably meant.
5. Record per-dimension scores in the table in §5. Mark any dimension the rater
   could not judge as `n/a` with a one-line reason; do not score it as 2.
6. Compute the session summary in §6. Report inter-rater agreement before
   reporting means — a mean over raters who disagreed wildly is not a result.

**Scoring does not require the rater to be a domain expert for every dimension.**
Dimensions 1, 2, 5, 6, 8 and 9 need only careful reading; 3, 4 and 7 benefit from
operational experience, and the rater should say so in the notes.

---

## 2. What "good" means here

A good OpsPilot report is a *defensible, bounded* answer to an incident question.
It is not the most confident answer, the longest answer, or the one with the most
citations. It is the one an on-call engineer could take to a review without
having to re-derive it, and could forward without embarrassment if the
hypothesis turned out to be wrong.

Two failure styles matter equally and are scored in separate dimensions:

- **Overreach** — asserting a cause the evidence does not carry.
- **Underreach** — refusing to say anything useful when the evidence did support
  a bounded statement.

A system that never overreaches by never saying anything is not good; dimension 6
exists to keep that from being the cheapest way to score well.

---

## 3. Dimensions and anchors

Scale for every dimension: **0 = fails, 1 = poor, 2 = adequate, 3 = good,
4 = excellent.** Half points are not allowed; use the notes field for nuance.

### D1. Grounding (evidence fidelity) — weight 3

Does every factual claim trace to a retrieved chunk?

| Score | Anchor |
| --- | --- |
| 0 | Contains a specific factual claim (log line, metric, config value, error text) with no supporting chunk, or attributes evidence to the wrong source. |
| 1 | Most claims are grounded, but at least one specific claim is unsupported or mis-cited. |
| 2 | All specific claims are grounded; some paraphrasing drifts beyond what the chunk says. |
| 3 | Every specific claim is grounded and paraphrases stay within the source. |
| 4 | As 3, and the report makes the link explicit enough that the rater verified each claim without hunting. |

### D2. Citation usefulness — weight 2

Citations exist (Phase 22 already checks validity); this scores whether they
*help*.

| Score | Anchor |
| --- | --- |
| 0 | No citations, or citations are a block list at the end unrelated to the sentence they support. |
| 1 | Citations present but attach to the wrong sentence, or cite a whole document for a specific claim. |
| 2 | Correct citations, placed loosely. |
| 3 | Each hypothesis and recommendation cites the chunk that motivates it. |
| 4 | As 3, and a rater can follow the reasoning chain by reading only the cited chunks in order. |

### D3. Root-cause plausibility — weight 3

Judged against the evidence and general operational knowledge, *not* against
whether it is the true cause.

| Score | Anchor |
| --- | --- |
| 0 | Contradicts the evidence, or is a category error (e.g. blames DNS for a parse error with a clear stack trace). |
| 1 | Technically possible but ignores the strongest available signal. |
| 2 | Plausible and consistent with the evidence, stated at a generic level. |
| 3 | Plausible, specific, and names the mechanism connecting evidence to symptom. |
| 4 | As 3, and distinguishes itself from the most likely competing explanation. |

### D4. Evidence discrimination — weight 3

Does the report separate what the evidence shows from what it guesses?

| Score | Anchor |
| --- | --- |
| 0 | Presents a hypothesis as established fact. |
| 1 | Uses confident language for inferred claims; hedging appears only in a generic disclaimer. |
| 2 | Generally distinguishes observation from inference, with at least one blurred claim. |
| 3 | Observations, inferences and unknowns are consistently distinguishable in the prose. |
| 4 | As 3, and the report states which observation *would* confirm or refute each hypothesis. |

### D5. Actionability — weight 3

Could an on-call engineer act on the recommendations without further triage?

| Score | Anchor |
| --- | --- |
| 0 | No recommendations, or only "investigate further". |
| 1 | Recommendations are generic advice ("check logs", "monitor") with no link to this incident. |
| 2 | At least one recommendation is specific and tied to the evidence. |
| 3 | Recommendations are specific, ordered by value, and each says what result would confirm the hypothesis. |
| 4 | As 3, and each recommendation states its risk or blast radius. |

### D6. Appropriate abstention — weight 3

Does the report refuse when the evidence does not support a conclusion — and only
then?

| Score | Anchor |
| --- | --- |
| 0 | Asserts a root cause when the evidence is clearly insufficient, **or** abstains when strong direct evidence is present. |
| 1 | Leans the wrong way for this evidence set, or abstains with no explanation of what is missing. |
| 2 | Correct decision, weakly justified. |
| 3 | Correct decision, and the report names the specific missing evidence. |
| 4 | As 3, and names the cheapest concrete step that would supply it. |

Note: a report scored 0 here for *under-refusal* is a safety failure and caps the
report's overall band at "unusable" regardless of other scores (§6).

### D7. Technical accuracy — weight 2

Are the technical statements correct?

| Score | Anchor |
| --- | --- |
| 0 | A statement is wrong in a way that would mislead remediation. |
| 1 | Minor technical errors that a competent reader would catch. |
| 2 | Correct but imprecise. |
| 3 | Correct and precise. |
| 4 | As 3, and adds correct context the rater did not expect (e.g. the actual default value, the real failure semantics). |

### D8. Clarity and structure — weight 1

Can a reader find the answer, the evidence and the next step quickly?

| Score | Anchor |
| --- | --- |
| 0 | Unreadable: no structure, or the summary contradicts the body. |
| 1 | Wall of text; key claim buried. |
| 2 | Readable, generic structure. |
| 3 | Summary states the claim; sections follow the question → evidence → cause → action arc. |
| 4 | As 3, and the summary alone would be a usable incident comment. |

### D9. Scope discipline — weight 2

Does it stay inside the question and the evidence, without padding?

| Score | Anchor |
| --- | --- |
| 0 | Invents scope: unrelated systems, speculative architecture, or recommendations far outside the incident. |
| 1 | Notices the actual question late; significant irrelevant content. |
| 2 | On topic with some filler. |
| 3 | Tightly on topic. |
| 4 | As 3, and explicitly notes what it deliberately left out of scope. |

---

## 4. Report-level band

Compute the weighted mean of D1–D9 (weights above, `n/a` dimensions dropped and
weights renormalised). Then apply the caps:

| Band | Weighted mean | Caps that override the mean |
| --- | --- | --- |
| **Unusable** | < 1.5 | D1 = 0 (fabricated fact) or D6 = 0 (wrong abstention decision) forces this band. |
| **Weak** | 1.5 – 2.4 | D3 ≤ 1 caps at Weak. |
| **Usable with review** | 2.5 – 3.1 | — |
| **Usable** | 3.2 – 3.6 | — |
| **Strong** | ≥ 3.7 | Requires D1 ≥ 3 and D6 ≥ 3; otherwise cap at Usable. |

Compute each rater's band independently, then report the band the raters agree
on. If they disagree by more than one band, the report is `disputed` and is
discussed before any aggregate is published.

---

## 5. Session record (fill in)

### Session header

| Field | Value |
| --- | --- |
| Date | |
| Commit | |
| Mode | `offline` / `local` / `live` |
| Model | |
| Dataset version | |
| Rater A | |
| Rater B | |
| Packet size | |

### Per-report scores

| Report | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | Weighted | Band |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R-01 A | | | | | | | | | | | |
| R-01 B | | | | | | | | | | | |
| R-02 A | | | | | | | | | | | |
| R-02 B | | | | | | | | | | | |

### Notes per report

```
R-01
  rater A: <one or two sentences: the single most important reason for the band>
  rater B:
  disputed dimensions:
```

---

## 6. Session summary (fill in)

| Metric | Value |
| --- | --- |
| Reports rated | |
| Bands agreed | |
| Reports disputed | |
| Mean weighted score (rater A) | |
| Mean weighted score (rater B) | |
| Mean absolute difference per dimension | |
| Dimensions where raters differed by ≥ 2 | |
| Safety failures (D1 = 0 or D6 = 0) | |

**Agreement first.** Report `mean absolute difference per dimension` before any
mean score. A dimension with a difference of ≥ 2 across raters is not a stable
measurement and must not be quoted as a quality result; either the anchors are
ambiguous (fix them) or the reports are genuinely borderline (say so).

**Thresholds.** No quality threshold is defined here. Defining one requires
several sessions across model versions to know the spread, and inventing one now
from zero data would be exactly the kind of unearned number this project has been
avoiding. What can be said after one session is relative: this system version
versus that one, on this packet, with this agreement level.

---

## 7. Known limitations of this rubric

- **Cost.** Two independent raters × 10–15 reports × 9 dimensions is roughly
  2–4 hours per session. It does not run in CI and cannot gate a merge.
- **Rater effects.** Operational experience correlates with stricter D3/D5
  scoring. Record experience level per rater; do not compare sessions with
  different rater pools.
- **Packet bias.** 10–15 reports from one dataset version cannot represent the
  incident distribution. A session measures *this packet*, not the product.
- **Anchors are untested.** The 0–4 anchors were written before any scoring. If
  the first session finds two raters disagreeing on D4 or D9, the anchors — not
  the raters — are the likely defect.
- **No LLM-as-judge substitute.** Adding one would need its own validation
  against human scores, which requires the human sessions first. Until then an
  automated quality score would be an unvalidated number wearing a metric's
  clothes.
