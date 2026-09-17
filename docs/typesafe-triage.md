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

Those four entries are **shape examples**, not the final grid: the descriptive scale, the
thresholds and the weights remain to be written and iterated. The grid is tuned in
`config/questions.toml`, never in code.

## Combination in code

The answers are aggregated by an explicit formula, with readable and editable coefficients:

```python
def score_item(answers: dict) -> float:
    """Aggregate the typed answers of one item into a single weighted score."""
    interest = answers["interest"]["score"]
    density = answers["density"]["score"]
    primary = answers["primary_source"]["noul"]
    return (0.5 * interest) + (0.3 * density) + (0.2 * primary)
```

Two rules attached to that aggregation:

- **Confidence filters before ranking.** An item whose confidence falls below the configured
  threshold is dropped without discussion, even when its score is good.
- **The ranking is logged**, not just the result: every item's score is kept so the grid can
  be contested after the fact.

## Costs

Sizing hypotheses (to be replaced by measurements at the first real run): **200 items per
night**, **1,500 state tokens per item**.

Update of 2026-09-17: collection now keeps every candidate of the day (984 on 2026-09-16), so
the number of items entering triage has become a triage-side setting, still to be chosen. The
figures below still assume 200.

| Item | Volume | Cost |
| --- | --- | --- |
| Total state sent | 0.30 million tokens per night, 9.0 million per month | — |
| Jev triage | input at USD 0.042 per million tokens, output free | **USD 0.38 per month** |
| Same volume with an LLM at USD 1 per million on input | plus an estimated 2.40 million output tokens per month | about USD 11 per month |
| Same volume with an LLM at USD 3 per million on input | same | about USD 34 per month |
| Pessimistic hypothesis: each of the 4 questions consumes the state again | 36 million tokens per month | USD 1.51 per month |

An honest reading of that table: the gain is real (a factor of 30 over the month) but **the
absolute amounts are negligible either way**. The case for Jev therefore does not rest on
money, but on the reliability of the format, the absence of paid retries and parallelisation
without degradation.

The real economic gain comes from elsewhere: triage reduces 200 items to 8, so the generative
LLM is never paid for 200 full texts. It is that filtering ratio which carries the saving, not
the unit price.

## Limits and watch points

- **Closed beta**, recent player: the call must go through an adapter
  (`clients/typesafe.py`) that can be swapped for a small local model, without touching the
  rest of the pipeline.
- **Announced speed not verified**: the claim of being "up to 200 times faster" comes from
  the vendor's communication, not from a reproduced measurement.
- **A local ranking model is free at the margin** on the target machine. Against it, Jev is
  not cheaper in money, but it is in time, in confidence calibration and in format guarantees
  — and it leaves the GPU free for the V3 TTS.
- **Order of magnitude**: the volumes above are sizing hypotheses. The first real run must
  replace them with measurements, and this note must be updated accordingly.
