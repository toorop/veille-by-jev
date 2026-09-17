# TypeSafe triage

## Purpose

Describe the judgement stage of the pipeline: what is sent (the state), what is asked (the
questions), and how the answers are combined.

Back to the index: [veille-by-jev](../README.md) · upstream stage:
[Pipeline workflow](pipeline-workflow.md).

## Why a decision model rather than an LLM

Jev (TypeSafe) generates no text: it evaluates typed questions against a state and returns
structured values with a probability distribution and a confidence score. Three direct
consequences for this project:

1. **No malformed JSON.** The answer is typed by construction: no parsing, no paid retry on
   an invalid output.
2. **No billed reasoning text.** Which is precisely what a generative LLM charges most for
   on a scoring task.
3. **Parallel, isolated evaluation.** The questions about one item are evaluated
   independently of each other: adding a question does not degrade the other answers. That is
   what allows a fine-grained grid instead of a single, fuzzy score.

The model is a **ranker, not a reader**: it writes nothing. Writing the digest stays the job
of a generative LLM, on a number of items reduced by triage.

## Definition of the state

What the ranker receives for one item — kept short, since only the input is billed:

- title;
- source and category (Hacker News section, subreddit where relevant);
- source score and comment count;
- age of the publication;
- URL type: primary source (paper, official announcement, code repository), press article,
  blog post, discussion;
- body text truncated to roughly 2,000 estimated tokens, or nothing at all when the extraction
  was too thin to be an article;
- retained Hacker News comment excerpts (V2 as far as tuning goes).

## The three primitives, applied to this project

| Primitive | Question | Usable answer |
| --- | --- | --- |
| `Score` | Interest for a French-speaking developer who follows applied AI and local tooling, on a descriptive five-level scale | score + distribution + confidence |
| `Score` | Technical density: from research paper to purely marketing announcement | score + confidence |
| `Choice` | Category: research, tooling, hardware, industry, society | chosen option + probability per option |
| `Noul` | Is this a primary source (paper, official announcement, code)? | value between 0 and 1 |

Those four entries are implemented as written, and they can be replaced or extended without
touching code: the grid is tuned in `config/questions.toml`. The weights in force are `interest`
0.5, `density` 0.3 and `primary_source` 0.2; the `category` carries none, being a label for the
digest rather than a quality signal. The descriptive scales are written in English, because the
engine is the one reading them.

## Combination in code

The answers are aggregated by an explicit formula, with readable and editable coefficients:

```python
def item_aggregate(answers, specs, aggregation, penalty_z) -> float:
    """Aggregate the typed answers into one ranking value, on the configured scale."""
    total = 0.0
    for name, weight in aggregation.normalised().items():
        value, _spread = component_value(answers[name], specs[name], penalty_z)
        total += weight * value
    return total * aggregation.scale
```

Every component is normalised to [0, 1] before weighting — a `Score` by its level count, a
`Noul` as is — and the aggregate is then expressed on the level scale. The scoping notes'
original formula added a position on 0–4 to a probability on 0–1, which gave the primary-source
signal a real weight of 5 % where it looked like 20 %. The weights are relative and normalised,
so they only have to express importance, and a question left out of them is recorded without
ranking: that is the case for the category, which is a label for the digest rather than a
quality signal.

Two rules attached to that aggregation:

- **The ranking uses a lower bound, not the position.** An item is ranked on
  `score - z × spread`, where the spread is the standard deviation of the level distribution
  the engine returns. Hesitating between two neighbouring levels costs little; hesitating
  between "no interest" and "essential" costs a lot. This replaced the notes' original rule —
  filtering on the confidence alone — after the first real run showed that rule ranked
  *backwards*: the best-scored item of the day had a confidence of 0.51 only because its mass
  sat between two high, neighbouring levels, while an item firmly at level 2 came back at 0.80.
  Confidence measures how precise a position is, not how high it is. Both knobs live in
  `config/questions.toml`: `score_penalty_z` (0 ranks on the raw score) and
  `min_adjusted_score`, below which an item is dropped without discussion. The confidence is
  still recorded, as a diagnostic.
- **The ranking is logged**, not just the result: every item's score is kept so the grid can
  be contested after the fact. The grid itself, and the distribution behind each answer, are
  written into `scores.json` for the same reason: another rule can be computed from them later
  without calling the engine again.

## Costs

Measured on the first real run (2026-09-16, five items), which replaces the sizing hypotheses:
**200 items per night** and **2,403 input tokens per item**.

Update of 2026-09-17: collection now keeps every candidate of the day (984 on 2026-09-16) and
enrichment caps what enters triage at 200, so the figures below are for 200 items.

| Item | Volume | Cost |
| --- | --- | --- |
| Total state sent | 0.48 million tokens per night, 14.4 million per month | — |
| Jev triage, measured | input at USD 0.042 per million tokens, output free | **USD 0.61 per month** |
| Same volume with an LLM at USD 1 per million on input | plus an estimated 2.40 million output tokens per month | about USD 14 per month |
| Same volume with an LLM at USD 3 per million on input | same | about USD 43 per month |

An honest reading of that table: the gain is real (a factor of 20 over the month) but **the
absolute amounts are negligible either way**. The case for Jev therefore does not rest on
money, but on the reliability of the format, the absence of paid retries and parallelisation
without degradation.

One earlier hypothesis was wrong and is worth recording: the pessimistic line that had each of
the four questions consume the state again (36 million tokens a month) does not describe the
API. One call carries the state and every question, and the state is ingested once, so adding
questions costs tokens for the questions themselves and nothing more.

The real economic gain comes from elsewhere: triage reduces 200 items to 8, so the generative
LLM is never paid for 200 full texts. It is that filtering ratio which carries the saving, not
the unit price.

## Limits and watch points

- **Closed beta**, recent player: the call must go through an adapter
  (`clients/typesafe.py`) that can be swapped for a small local model, without touching the
  rest of the pipeline.
- **Not deterministic.** Two runs over the same five items gave scores differing by up to 0.05
  and confidences by about 0.01. A threshold sitting exactly on an observed value can
  therefore flip between runs, and `--force` does not reproduce a ranking bit for bit.
- **Measured latency**: about 0.6 s per call on a single item, against the 70 to 500 ms
  announced by the vendor. Consistent with a five-level question on a 2,400-token state, but
  it is a measurement on five items, not a benchmark.
- **The billing basis is assumed.** The printed cost applies the price list to `input_tokens`
  because the provider's own `billing_units` field never reaches the public response object;
  the first invoice is what will confirm it.
- **A local ranking model is free at the margin** on the target machine. Against it, Jev is
  not cheaper in money, but it is in time, in confidence calibration and in format guarantees
  — and it leaves the GPU free for the V3 TTS.
- **Order of magnitude**: the volumes above come from five items on one night. A full run over
  a varied batch is what would make them solid.
