# Defect-driven KB optimization

Coverage answers "what material do we have". It does not answer "where do models actually get this wrong". Those diverge more than expected: a topic with three guidelines behind it can still be the one every model fabricates, and a thinly-sourced topic can be one they all handle fine.

When a project has an evaluation export — questions × models × scored dimensions — that export is a direct measurement of the second question. This document is how that measurement turns into KB structure.

## The pipeline

```
eval export ──analyze_eval.py──▶ defect-profile.json ──agent reads──▶ 弥补项
                                 缺陷图谱.md                              │
                                                                        ▼
KB JSON (entities + 4 remediation fields + antipatterns + 易混淆对)
                                                                        │
                            validate_kb.py --defects ◀──────────────────┘
```

The script does **mechanical aggregation only**: counts, rankings, keyword frequencies, verbatim samples. It never decides what a cluster *means*. That judgement is the agent's, and it requires reading the samples — the same division of labour as everywhere else in this skill (scripts handle the mechanical, the agent handles the judgement).

## Defect → KB primitive

Every recurring failure mode a rubric can measure maps onto a structure this skill already had, or one of the four fields added for the purpose:

| Failure mode | What it looks like | KB primitive |
|---|---|---|
| **Overclaiming** | Attributing effects the evidence does not support (a supportive drug described as extending survival) | `边界与禁止表述` states the evidence ceiling; `合规备注` carries `【…·合规禁区】` phrasing bans; an anti-pattern card shows the wrong sentence next to the right one |
| **Entity confusion** | Two similar products/doses/populations merged; specs of A attributed to B | `appendix.易混淆对` (the explicit do-not-merge assertion) + `易混淆辨析` on both entity cards |
| **Fabrication** | Invented PMIDs, guideline versions, precise figures, citation markers with no matching reference | Claim-level `evidence` on every hard number. `validate_kb.py` warns on entries carrying figures with nothing in `evidence` |
| **Safety under-reporting** | A danger sign mentioned but not acted on, or omitted entirely | `风险信号与行动` — trigger → action pairs, in that order |
| **Non-actionable answers** | Correct as far as it goes, then ends in "consult your doctor" | `可执行下一步` — name the test, the timing, the threshold, or the person to ask |
| **Self-inconsistency** | The same fact answered differently across questions | Single source of truth: one canonical entity, one `evidence` block. The KB structure solves this; the validator's normalization checks catch regressions |

Note how little of this is new machinery. Entity confusion *is* the over-merging defect this skill has always warned about (`同义(=)` vs `is-a`), showing up in production. Fabrication is exactly what claim-level `evidence` exists to prevent. The evaluation data mostly tells you **which existing discipline to enforce hardest, and where**.

## The `universal` criterion

The most useful signal in the profile is not "how often does this fail" but "**does every model fail it**".

The reasoning: models are trained on overlapping public corpora. When one model gets something wrong and four get it right, that is a capability difference — a KB can help, but the knowledge is out there. When **every** model fails the same thing, the knowledge is missing, ambiguous, or contradictory in the public corpus. That is precisely the gap a curated KB exists to fill, and it earns P0 regardless of volume.

But granularity decides whether the signal means anything. Three candidate definitions, two of which collapse:

| Definition | Problem |
|---|---|
| "All models fail dimension D" (fleet-wide) | Near-tautological. With a thousand responses, any dimension failing often enough involves every model. On real data this fired for 5 of 5 dimensions. |
| "No model passed question Q" | On a strict rubric where the fleet pass rate is a few percent, this is true of ~95% of questions. On real data: 234 of 246. |
| **"Every scored model failed dimension D on question Q"** | Discriminating (15 of 246 on the same data), and it names *both* the question and the kind of knowledge missing. |

The third is what `question_hotspots[].universalDimensions` reports, and it is the list the materials-list ordering should follow. A hotspot reading `D1,D2,D3 + R1` on a dosing question tells you: no model has the correct dosing facts, and they confuse the products while getting it wrong. That is a shopping list entry, not a statistic.

One practical adjustment: exports routinely contain unscored responses (timeouts, API errors). Demanding a literal full house would let an infrastructure gap mask a knowledge hole, so the analyzer requires a quorum of scored models (`MIN_HOTSPOT_COVERAGE`, default 60%) rather than all of them.

## Priority bands

`clusters[].priority` bands each defect P0/P1/P2. Two rules:

- **`universal` forces P0**, whatever the volume.
- Otherwise, band by **share of all scored responses** — one denominator for every cluster type, so the bands are comparable. Thresholds differ by severity: a red line (一票否决) at 5% is worse than a dimension scoring zero at 5%, so red lines cross into P0 sooner.

Getting the denominator right matters. Scoring red lines as a share of *red lines* rather than of *responses* inverts the ranking — it can put the single largest 一票否决 defect below a minor coverage dimension.

`topic_risk` bands separately, by mean score per question type. Use it for breadth (which themes need work); use `clusters` and `question_hotspots` for depth (what specifically to write).

## Evaluation-export field contract

The analyzer ships with a built-in profile for this shape:

```
{ questions: [ { questionId, question, questionType, cancerType,
                 modelResults: [ { model,
                                   scoring: { status, dimensions: {D1: {score, reason}, ...},
                                              redLines: [{code, evidence}],
                                              totalScore, passed, mainFlaw, fabrications } } ] } ],
  task: {taskName}, scoring: {judgeModel, rubricTitle, rubricVersion, dimensions[], redLines[]} }
```

For a different evaluation system, override any subset of paths with `--field-map`:

```json
{
  "questions": "items[]",
  "model_results": "runs[]",
  "model_id": "engine",
  "scoring": "grade",
  "scoring_ok_value": "done",
  "dimensions": "criteria",
  "dimension_score": "value"
}
```

Paths are dot-separated; a trailing `[]` marks a list. Unknown keys are rejected rather than silently ignored. Scores are read as `1 / 0.5 / 0` — a defect is a `0`. If your rubric uses a different scale, normalize before feeding it in.

If the export parses but yields no scored responses, the analyzer exits 2 and names `scoring_ok_value` as the likely mismatch — that is nearly always the field that differs first.

## Writing anti-pattern cards

An anti-pattern card records a real wrong answer alongside the correct one. Ordinary fields say what is true; a card says what was actually said wrongly and why, which is what stops it recurring.

Three rules, each of which a card fails without:

1. **Quote, don't paraphrase.** `错误表述` is the model's actual output, from the profile's `evidence_samples` or `flawSamples`. A cleaned-up paraphrase loses the exact phrasing that has to be recognized.
2. **Always give `正确说法`.** A card that only forbids leaves the reader with nothing to say instead, and the gap refills with the same wrong answer. The validator warns when `正确说法` is too short to be a usable replacement.
3. **The correct version carries a source.** Otherwise the card swaps one unsourced claim for another — replicating the defect it was written to fix.

Carry `命中模型` / `命中次数` / `universal` across from the profile. They let a reviewer tell at a glance whether a card addresses a systemic gap or one model's quirk.

## Validating coverage

```bash
python3 scripts/validate_kb.py your-kb.json --defects defect-profile.json
```

Adds a 缺陷覆盖对照 table to `质量报告.md` and errors when a P0 defect has no matching anti-pattern card. Without `--defects` the check is skipped with a note — the other checks still run.

Coverage here means *addressed*, not *fixed*. The validator confirms a P0 defect has a card carrying its code; whether that card actually corrects the underlying error is a judgement only review can make. Treat a clean coverage table as a floor, not a finish line.

## When to skip all of this

Step 3 is optional and should be skipped when there is no evaluation data. Do not manufacture a defect profile from intuition about what models "probably" get wrong — the entire value here is that the priorities are measured. Without measurement, build from the coverage matrix and the source hierarchy as before.