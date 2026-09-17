# Implementation journal

Back to the index: [veille-by-jev](../README.md).

## 2026-09-17 — scoping

The project was born from a detour: the original research was about local voice cloning. The
conclusion of that exploration redefined the project instead of closing it.

### What was rejected, and why

| Track | Verdict | Reason |
| --- | --- | --- |
| Local voice cloning (LuxTTS, XTTS, Chatterbox) | dropped for this project | LuxTTS and IndexTTS do not handle French; Chatterbox does, but cloning is not needed for an audio digest |
| Cloning through a cloud API | rejected | Voice and text would leave the machine, which contradicts the rest of the project |
| `tts-serve` as the engine layer | rejected for V1 | Its value is multiple engines and cloning; without cloning it is one dependency too many |
| NotebookLM, Podcastfy as a foundation | rejected, kept as references | They transform whatever sources they are given; here the agent must decide **what deserves attention** |
| Rust for the pipeline | rejected | A few milliseconds saved on a nightly job, at the cost of slower iteration |

### What was decided

- The V1 deliverable is **written**, not spoken.
- The triage engine is Jev (TypeSafe), behind an adapter.
- The scoring grid lives in a configuration file, not in a prompt.
- Stages are replayable commands that communicate through files.

### What was verified on the same day

- Hacker News API: Algolia and Firebase both respond.
- Reddit: RSS works unauthenticated but carries neither score nor comments; the JSON endpoint
  is blocked.
- arXiv: responds over HTTPS.
- Two French-language feeds (Le Monde, Next) respond.
- Jev pricing: input at USD 0.042 per million tokens, output free.

### Still to verify

- The real cost per run, to be measured at the first attempt.
- Jev's speed on a batch of items.
- The real failure rate of text extraction across varied articles.
- The quality of French writing from English sources, on a real batch.

## 2026-09-17 — stage 1: collection

The code repository now lives at the project root, at the same level as `docs/`: the Obsidian
vault is no longer the container. The project is called **veille-by-jev**, the short command
**`vbj`**, and the Python package stays `veille/`.

### What was built

- `vbj collect --date YYYY-MM-DD`: a 24-hour window cut as a civil day in the configured
  timezone, one Algolia request, ranking by points, deduplication by URL, atomic write of
  `data/<date>/items.json`.
- Replay without `--force`: nothing is redone, no network call.
- A source failure is logged into the file without bringing the stage down; an unusable item
  is dropped harmlessly.
- 44 tests (pytest) and a configured linter (ruff), with no test touching the network.

### Measured on the day of 2026-09-16

| Quantity | Value |
| --- | --- |
| Stories published inside the window | 1,139, of which 1,000 are reachable (Algolia pagination cap) |
| Raw items received | 1,000 |
| Candidates kept (points ≥ 1) | 984, all written |
| HTTP requests / duration | 1 request, about 0.9 s |
| Size of `items.json` | 423,491 bytes |
| Publication range covered | 2026-09-15T22:00:28Z → 2026-09-16T21:59:47Z |
| Model calls, cost | 0, USD 0.00 |

### Decisions taken at this stage

- **Civil day**, not a rolling 24 hours: the same `--date` always yields the same batch.
- **`items.json` is not truncated**: collection costs one request, the cap belongs to the
  stage that pays. `config/sources.toml` therefore no longer carries an "items aimed for".
- **English for code, CLI and configuration; French for the digest alone**, since the
  repository is public and the digest's reader is French-speaking.
- **The linter and the tests join the project**, with their configuration, instead of being
  run by hand.

### What was rejected

- Truncating `items.json` to the digest size: that would make any re-tuning of the grid
  depend on collecting again.
- A rolling 24-hour window: a 02:00 run would miss the end of the Hacker News evening, and two
  runs of the same date would not yield the same batch.

## 2026-09-17 — stage 2: enrichment

### What was built

- `vbj enrich --date YYYY-MM-DD`: fetches the best candidates of the day, extracts the main
  article text, truncates it to a token budget and collects the first Hacker News comments,
  one JSON file per item under `data/<date>/enriched/`.
- The cache is keyed by URL fingerprint and holds only what the article said, never source
  metadata: a score that drifts over time cannot invalidate it. A second run downloads nothing.
- Extraction goes through `trafilatura` in plain-text mode: markup, menus, cookie banners and
  ads never reach the token budget. Measured on a BBC article, 307,079 characters of HTML
  become 6,071 characters of prose — 2.0% of the raw page.
- Truncation is placed at a sentence boundary when one is close enough to the budget.
- An article that cannot be read, or that yields almost nothing, becomes an `unavailable`
  record instead of a failed run.
- 69 tests, still with no test touching the network.

### Measured on a development run of 20 items (2026-09-16)

The full 200-item run was deliberately not launched during development; these figures come
from a 20-item run, and the 200-item lines are extrapolations, not measurements.

| Quantity | Value |
| --- | --- |
| Items enriched | 20 fetched, no cache hits |
| Items with usable text | 17 |
| Items marked metadata-only by the floor | 3 |
| Estimated text tokens, readable items | 25,056 total, 1,517 median, 1,990 maximum |
| Items still cut at the 2,000-token ceiling | 8 of 17 |
| Hacker News comments kept | 85, five per readable item |
| HTTP requests | 122 in 80 s |
| Extrapolation for 200 items | about 1,200 requests and 10 to 15 minutes |

The three metadata-only items are the interesting result: a Mastodon post (34 estimated
tokens), a Salesforce status page (11) and a Xiaomi JavaScript dashboard (10). Without a floor,
all three would have entered triage as if they were articles.

### Decisions taken at this stage

- **The cap of 200 items lives here**, not in collection: this is the stage where fetching
  starts to cost time, and it is where the state of the priced stage begins.
- **A 2,000-token ceiling and a 200-token floor.** At 1,200 the median article was cut for no
  reason (measured median: about 1,100 tokens), and at the other end a 10-token extraction was
  being called an article.
- **The five first top-level comments**, in the order the API returns them. That order is
  insertion order, close to chronological but not sorted by time — an honest limitation.
- **A failed thread does not discard the article**: the text is cached and the failure is
  recorded in `comments_error` rather than retried.

### What was rejected

- Keeping the HTML: markup has no semantic value and would consume the token budget that is
  the main cost lever of triage.
- Keeping the comments published on the article page: the discussion worth reading is the
  Hacker News thread, which is collected separately.
- Refusing to cache an item whose thread failed: the article text is the deliverable, and a
  network hiccup on a comment should not cost another article fetch.

## 2026-09-17 — stage 3: triage

### What was built

- `vbj triage --date YYYY-MM-DD`: reads `items.json`, keeps the enriched candidates, asks the
  questions of `config/questions.toml` to Jev — one call per item — and writes
  `data/<date>/scores.json` with every typed answer, its confidence and its distribution.
- The grid is snapshotted into `scores.json` alongside the scores, so a ranking can always be
  traced back to the questions that produced it.
- The confidence filter runs before the ranking: an item whose weakest confidence falls below
  the threshold is dropped without discussion.
- `.env` is read at startup, so a locally stored key works without any shell setup; a variable
  already exported always wins over the file.
- 116 tests, still with none touching the network or the provider.

### What the first real run measured (5 items, 2026-09-16)

| Quantity | Hypothesis | Measured |
| --- | --- | --- |
| Input tokens per item | 1,500 | **2,403** |
| Estimate against the invoice | — | 9,862 estimated, 12,016 billed: a factor of 1.22 |
| Monthly triage cost at 200 items | USD 0.38 | **USD 0.61** |
| Cost of this run | — | USD 0.000505 |
| Latency per call | 70 to 500 ms announced | about 0.6 s |

The scores separate applied AI and tooling (1.93 to 2.80) from a political story (0.20), which
is what the five levels were written for.

### The engine is not deterministic

Two runs over the same five items gave slightly different answers:

| Item | Run 1 | Run 2 |
| --- | --- | --- |
| Nvidia announces native GPU programming in Rust | 2.750 / 0.500 | 2.800 / 0.510 |
| Mistral X Mozilla | 2.100 / 0.800 | 2.080 / 0.800 |
| Training a 4B model for faster query plans | 2.010 / 0.870 | 2.020 / 0.880 |

Scores move by up to about 0.05 and confidences by about 0.01. `--force` therefore does not
reproduce the same ranking exactly: the idempotence invariant protects the file and the money,
not the bit-for-bit reproducibility of a judgement. A threshold sitting exactly on an observed
value can flip between runs.

### Decisions taken at this stage

- **The grid is written in English.** The engine reads it, and English is what it handles best;
  French stays reserved for the digest, the only thing a human reads.
- **The confidence filter was replaced by a lower-bound ranking.** The first rule filtered on
  the confidence alone, as the notes prescribed. The first real run showed it ranked
  *backwards*: the best-scored item of the day (2.80) came back at a confidence of 0.51
  **because** its mass sat between levels 2 and 3 — two high, neighbouring levels — while an
  item firmly at level 2 came back at 0.80. Confidence measures how precise a position is, not
  how high it is. Ranking now uses `score - z × spread`, where the spread is the standard
  deviation of the level distribution: hesitating between neighbouring levels costs little,
  hesitating between "no interest" and "essential" costs a lot. Measured effect on the same
  five items: the Nvidia story goes from dropped to first, with an adjusted score of 2.08, and
  the political story stays last at −0.22 and is the one that gets dropped.
- **Two knobs rather than one**, both in `config/questions.toml`: `score_penalty_z`, how many
  standard deviations of downside to subtract (0 ranks on the raw score), and
  `min_adjusted_score`, below which an item is dropped.
- **One call per item.** The state is one article and the questions are about it. Batching
  several items into one call would need per-item question names and would blur the state.
- **The printed cost applies the price list to `input_tokens`.** The provider's own
  `billing_units` field never reaches the public response object, so the first invoice is what
  will confirm the basis.

### What was rejected

- **A French grid.** It reads better for a human, but no human reads it: the engine does.
- **Trusting the character-based token estimate without calibrating it.** The measured factor is
  1.22; treating 4.0 characters per token as exact understated every cost estimate by a fifth.
- **Parsing the response in a way that loses the accounting.** The first attempt failed inside
  normalisation *after* five calls had been billed, and reported zero tokens. The usage is now
  carried on every path, including the failure one.
- **Filtering on the confidence scalar alone**, which the scoping notes prescribed. The
  measurement above is what refuted it. The confidence is still recorded, as a diagnostic.

## 2026-09-17 — stage 4: the full grid and the weighted aggregation

### What was built

The grid now holds the four questions the scoping notes proposed, and they are combined in
code:

| Question | Type | Weight | Role |
| --- | --- | --- | --- |
| `interest` | Score, 5 levels | 0.5 | the main signal |
| `density` | Score, 5 levels | 0.3 | from research paper to pure announcement |
| `primary_source` | Noul | 0.2 | a paper or an official announcement, not a report about one |
| `category` | Choice, 5 options | none | a label for the digest, not a quality signal |

Weights live in an `[aggregation]` table, are normalised before use, and a weight naming an
unknown question is rejected at load time: a typo there would silently drop a question from
the ranking.

### A defect in the notes' aggregation formula, fixed

The notes proposed `0.5 × interest + 0.3 × density + 0.2 × primary`, mixing a position on 0–4
with a probability on 0–1. Read literally, that gives the primary-source signal a real weight
of 5 % where it looks like 20 %. Every component is now normalised to [0, 1] before weighting,
and the aggregate is expressed back on the level scale so that `min_adjusted_score` keeps the
meaning it was calibrated with. On a worked example — an essential, shallow, primary item — the
note's formula gives 2.2 and the corrected one 2.8; a test locks that in.

### What the real run measured (same five items)

| Quantity | One question | Four questions |
| --- | --- | --- |
| Input tokens | 12,016 | 13,376 (+11 %) |
| Output tokens | 85 | 493 |
| Latency | 2.9 s | 2.6 s |
| Cost | USD 0.000505 | USD 0.000562 |

The three extra questions cost about a tenth more input, which matches the API's design: the
state is ingested once and the questions themselves are small.

### What the full grid changed in the ranking

| Item | `interest` alone | Weighted aggregate | Category |
| --- | --- | --- | --- |
| Nvidia announces native GPU programming in Rust | 2.08 | 2.38 | tooling |
| Training a 4B model for faster query plans | 1.62 | 1.61 | research |
| Mistral X Mozilla | 1.55 | 1.49 | industry |
| Small programming tricks | 1.53 | 1.38 | tooling |
| EU chief opens door for Canada | −0.22 | 0.07 | society |

Two things changed beyond noise. Training and Mistral swap: Mistral is a primary source (0.82)
but thin (density 0.05), while Training is the opposite (0.17 and 0.55). And the political story
is pulled up by the scale change — its aggregate is near zero rather than negative, since a
`Noul` cannot contribute a negative component — which is exactly why the admission floor has to
be re-calibrated on a real batch rather than on five items.

### Decisions taken at this stage

- **The category carries no weight.** It is a label the digest will use to group items; a
  category is not a quality signal, and weighting it would smuggle a judgement into the ranking.
- **A missing weighted answer is skipped, not counted as zero.** With one call carrying every
  question, an absent answer is an anomaly, and scoring it as "no interest" would be a silent
  lie.
- **The aggregate is discrete arithmetic over normalised components**, so the whole ranking can
  be recomputed from `scores.json` without calling the engine again. Every component is written
  next to the raw answers.

### What was rejected

- **Keeping the notes' formula as written**, which would have under-weighted the primary-source
  signal by a factor of four.
- **Weighting the category**, which would have made the digest's section labels influence the
  selection.

## 2026-09-17 — the full calibration run

The first real batch: 200 candidates of 2026-09-16, enriched then triaged end to end. This is
the run the scoping notes asked for, and it replaces the last sizing hypotheses with readings.

### Measured

| Quantity | Previous figure | Measured on 200 items |
| --- | --- | --- |
| Enrichment duration | 10 to 15 min, extrapolated from 20 items | **2 min 31 s** (718 requests) |
| Articles with usable text | 17 of 20 in a development run | 166 of 200; the other 34 became metadata-only |
| Input tokens per triaged item | 2,675, from a 5-item sample | **1,983** |
| Triage duration | about 2 min, extrapolated | **88 s**, so 0.44 s per call |
| Triage cost of a night | USD 0.67 extrapolated | **USD 0.0167** |
| Triage cost per month at 200 items | USD 0.67 | **USD 0.50** |

The 5-item sample had overestimated the token count by a third: those five were the longest,
most commented stories of the day, each hitting the 2,000-token ceiling with five comments.
Extrapolating from the top of a ranking measures the top of a ranking, not the batch.

### The distribution has no natural break

```text
  2.50 | ### 3
  2.25 | ###### 6
  2.00 | ########### 11
  1.75 | ########## 10
  1.50 | ############## 14
  1.25 | ############ 12
  1.00 | ############ 12
  0.75 | ########## 10
  0.50 | ################################# 33
  0.25 | ######################################### 41
  0.00 | ############################################ 44
 -0.25 | #### 4
```

Median 0.44, mean 0.71, maximum 2.62. The candidates above each floor: 0.5 keeps 93 items, 1.0
keeps 61, 1.5 keeps 38, 2.0 keeps 14, 2.2 keeps 6. Since the shape offers no gap to latch onto,
the floor cannot be read off the data: it is chosen from what we want to read.

### Three checks that give the grid credibility

- **No metadata-only item reaches the top.** All 14 items above 2.0 have extracted article
  text, so the ranking is not being convinced by titles alone.
- **The top 20 holds only `tooling` and `research`**, while the day itself was dominated by
  `society` (96 items out of 200) and `industry` (27). The grid discards what it was written to
  discard.
- The 14 leaders are, in order: AMD matrix cores, ternary LLMs, a router for agent tools, CUDA
  in Rust, recursive self-improvement, a reverse-engineered Jev-like model, Datamimic, DeepSeek
  v4.1 on an M5 Max, Common Crawl on a Hugging Face bucket, ImpactGate, text-to-image 3.6×
  faster, pull requests replaced by deltas, Kival, and Swift-Qwen3.8.

### Decisions taken at this stage

- **The admission floor is 2.0**, so an item must still look at least "useful" once its
  downside is subtracted. At 1.0 the night kept 61 items, far too many to read.
- **The digest keeps 8 items.** The floor guarantees a minimum quality on a quiet night, the
  digest size caps the volume on a busy one, and the two roles stay separate rather than being
  folded into one threshold.
- **Changing the floor will not cost another call.** The aggregate and its components are
  stored per item, so the writer re-applies the current floor from `scores.json`. The `passed`
  flag written by triage is a snapshot of the decision as of its own run, and is recorded as
  such.

## 2026-09-17 — stage 5: writing, and comparing three writers

### What was built

- `vbj write --date YYYY-MM-DD`: takes the items triage admitted, sends their state to the
  writing model in one call, and assembles the digest from the answer.
- **The model writes prose only.** The dated title, the links, the sources, the scores, the
  set-aside table and the cost line are generated from the data, so a model can neither
  misquote a figure nor forget a link. The answer is JSON keyed by item id, parsed tolerantly:
  a malformed reply becomes a reserve written into the digest rather than a broken file.
- The system prompt lives in `config/write-prompt.md`, and that is where the readability
  requirement lives: say what the thing is before saying why it matters, unpack jargon, short
  sentences, no sentence that has to be read twice. The prompt is English, like the grid, and
  demands French prose.
- The cost comes from the provider, not from a price list: OpenRouter's OpenAPI specification
  carries the real cost of the call in `usage.cost`. When it is absent the digest says so
  instead of inventing a figure.

### The comparison, on the same eight items

| Writer | Input | Output | Time | Cost of the night | Per month |
| --- | --- | --- | --- | --- | --- |
| `deepseek/deepseek-v4-flash` | 16,036 | 2,917 | 26.6 s | USD 0.0011 | USD 0.03 |
| `mistralai/mistral-medium-3` | 16,659 | 1,719 | 31.5 s | USD 0.0101 | USD 0.30 |
| `anthropic/claude-sonnet-5` | 24,109 | 3,655 | 40.9 s | USD 0.0848 | USD 2.54 |

The whole pipeline therefore costs about USD 0.53, 0.80 or 3.04 per month depending on the
writer, against USD 0.50 for the triage alone.

### What the comparison showed

- **A 4,000-token output ceiling was too low.** Claude's first answer was cut mid-string, which
  the pipeline caught and wrote into the digest as a reserve with eight missing summaries. The
  ceiling is now 8,000; the failure was visible rather than silent, which was the point of the
  reserve mechanism.
- **The cheapest model is correct but not pedagogical.** DeepSeek's French is sound and its
  summaries are short, but it stacks jargon without unpacking it: "ternaire", "AVX-512", "Xe2"
  arrive undefined.
- **Mistral is the most technically complete** and does unpack terms, but produced two French
  slips on the second item: "ce qui théorique nécessite" for "théoriquement", and "1,28x" for a
  multiplication.
- **Claude explains reasoning rather than listing figures**, which is exactly what the
  readability requirement asks for: on the ternary-weight paper it is the only one to say *why*
  the current format costs 1.625 bits per weight. It also wrote "tensor cores" for AMD's matrix
  cores, which is Nvidia's term: a simplification that misleads.
- Anthropic's tokeniser counts the same state as 24,109 tokens where the others count about
  16,000, so its cost per night is higher than the price list alone suggests.

### The writer chosen, and what that cost

`anthropic/claude-sonnet-5` writes the digest. It is the only one of the three that explains
reasoning instead of listing figures, which is what the readability requirement asks for, and
the difference in cost is a few dollars a month on a project that runs once a night.

The first answer from that model called AMD's matrix cores "tensor cores", which is Nvidia's
term for something else: a simplification that taught the reader something false. The system
prompt now carries an explicit rule — keep the term the article uses, do not swap one vendor's
term for a neighbouring one, and explain rather than substitute when a simplification would
change the meaning. On the same item the next run wrote "les multiplicateurs de matrices
intégrés aux GPU", then contrasted AMD's matrix cores with Nvidia's tensor cores correctly.

The hardened prompt makes the model write more: 7,230 output tokens for eight items, against
3,655 before. Measured on the contract digest: 24,242 input and 7,230 output tokens, 67 s,
USD 0.1208 for the night, so about USD 3.62 per month. The pipeline as a whole costs about
USD 4.12 per month, of which USD 0.50 is the triage.

## Measurements

Sizing hypotheses are replaced by readings as they come. Rows without a measurement belong to
stages not yet written.

| Quantity | Hypothesis | Measured | Date |
| --- | --- | --- | --- |
| Items collected per night | 200 | 984 candidates kept out of 1,000 received | 2026-09-17 |
| Items triaged per night | 200 | 200, of which 166 with article text | 2026-09-17 |
| HTTP requests for collection | not estimated | 1 | 2026-09-17 |
| Duration of collection | not estimated | 0.9 s | 2026-09-17 |
| Share of articles with usable text | not estimated | 166 of 200 in the full batch, 83 % | 2026-09-17 |
| State tokens per item | 1,500 | 2,403 billed tokens per item | 2026-09-17 |
| Text tokens per readable item | 1,500 | 1,517 median of text alone, comments excluded | 2026-09-17 |
| Character-based estimate against the invoice | — | a factor of 1.22 | 2026-09-17 |
| Duration of enrichment | not estimated | 2 min 31 s for 200 items, 718 requests | 2026-09-17 |
| Latency of one triage call | 70 to 500 ms announced | 0.44 s over 200 calls | 2026-09-17 |
| Input tokens per item | 1,500 | 1,983 over 200 items, against 2,675 on a 5-item sample | 2026-09-17 |
| Triage cost per month | USD 0.38 | USD 0.50 at 200 items per night | 2026-09-17 |
| Writing cost per month | not quantified | — | — |
| Total duration of the nightly run | not estimated | — | — |

## Checklist

- [x] `collect` operational on a real day
- [x] `enrich` operational, with cache and failure tolerance
- [x] `triage` operational with a single question
- [x] full grid and coefficients in `config/questions.toml`
- [x] `write` operational, digest in French
- [ ] first digest read all the way through by Stéphane
- [ ] real cost measured and reported in [TypeSafe triage](typesafe-triage.md)
- [ ] decision: move on to V2 (Reddit, arXiv) or adjust the grid

## Decision log

| Date | Decision | Motive |
| --- | --- | --- |
| 2026-09-17 | Text deliverable before any TTS | A bad digest is caught by reading, not by listening |
| 2026-09-17 | Triage by typed questions rather than a scoring prompt | Format reliability and zero output cost |
| 2026-09-17 | Python, not Rust | The criterion is iteration speed, not performance |
| 2026-09-17 | Window = civil day in a configurable timezone | Identical replay and full coverage of a Hacker News day |
| 2026-09-17 | `items.json` keeps every candidate | Collection is free; the cap goes to the stage that pays |
| 2026-09-17 | English code, CLI and configuration, French digest | Public repository, French-speaking reader |
| 2026-09-17 | ruff and pytest inside the project, with their configuration | Conventions and invariants stop depending on my discipline |
| 2026-09-17 | Enrichment cap of 200 items | First stage where fetching costs time, and the start of the state triage pays for |
| 2026-09-17 | 2,000-token ceiling, 200-token floor for article text | At 1,200 the median article was cut for nothing; at the other end a 10-token extraction was called an article |
| 2026-09-17 | Five first top-level comments, inserted order | The value is often in the thread; the API order is honest but not sorted by score |
| 2026-09-17 | Grid written in English, French reserved for the digest | The engine reads the grid; no human does |
| 2026-09-17 | Confidence threshold lowered from 0.6 to 0.50, then dropped entirely | On a five-level scale, 0.6 discarded the day's best-scored item at a confidence of exactly 0.500; the lower-bound ranking then replaced the filter |
| 2026-09-17 | Components normalised to [0, 1] before weighting | The notes' formula mixed a position on 0-4 with a probability on 0-1, deflating the primary-source weight from 20 % to 5 % |
| 2026-09-17 | Category recorded but not weighted | A category is a label for the digest, not a quality signal |
| 2026-09-17 | Admission floor 2.0, digest of 8 items | At 1.0 a night kept 61 items, too many to read; the floor guarantees a minimum quality, the size caps the volume |
| 2026-09-17 | The floor is re-applied downstream, not baked in | The aggregate is stored per item, so changing the floor costs no new call |
| 2026-09-17 | The digest must be readable by a non-specialist | A technical batch does not excuse an unreadable digest; it is to be listened to, so a sentence that needs re-reading is a defect. This belongs in the system prompt |
| 2026-09-17 | Writer model chosen through OpenRouter, on a measured comparison | The cost range from cheap open models to frontier ones is under three dollars a month, so price does not settle it; three candidates were run on the same stored batch and read |
| 2026-09-17 | The model writes prose only, the structure is generated | A model that can quote a figure can misquote it; links and scores come from the data |
| 2026-09-17 | Output ceiling raised from 4,000 to 8,000 tokens | A truncated answer cost a full call and produced eight missing summaries; the reserve made it visible instead of silent |
| 2026-09-17 | `anthropic/claude-sonnet-5` writes the digest | The only candidate that explains reasoning instead of listing figures; about USD 3.62 a month for the writer alone |
| 2026-09-17 | The prompt forbids substituting a neighbouring term | It had called AMD's matrix cores "tensor cores", Nvidia's term: a simplification that teaches something false |
