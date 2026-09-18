# Pipeline workflow

## Purpose

Describe the five stages of the pipeline, their inputs and outputs, and the invariants that
make the whole thing replayable and debuggable.

Back to the index: [veille-by-jev](../README.md).

## Architectural principle

Each stage is a **standalone command** that reads files and writes other files. No service,
no daemon, no state kept in memory between two runs.

Three reasons:

1. Replay triage after changing the scoring grid, without collecting again.
2. Inspect the intermediate state when the final result is bad.
3. Compare two days, or two versions of the grid, by diffing files.

## The five stages

### 1. Collection — `vbj collect [--date YYYY-MM-DD]`

- Input: source configuration.
- Output: `data/<date>/items.json` — the window covered, one report per source, run
  statistics, and one record per item: stable identifier, source, URL, title, source score,
  comment count, publication timestamp.
- No model call. A deliberately dumb stage.
- For Hacker News, the Algolia endpoint ranked by relevance is queried over the requested
  window: `https://hn.algolia.com/api/v1/search`, filtered by
  `created_at_i >= start, created_at_i < end`, one request at `hitsPerPage=1000`, then ranked
  locally by points. Querying only `front_page` would make the batch depend on the hour of the
  run, and was rejected.
- **Two windows, for two needs.** `--date` gives a civil day in the configured timezone: the
  same date always yields the same batch, which is what makes a night replayable and two
  writers comparable on the same state. Without `--date`, the window is the last
  `window_hours` ending now, labelled by the civil day it ends in: the digest never lags, at
  the price that the same label no longer means the same batch. Because of that, a run is
  identified by the window stored in `items.json`, not by its date — a second `collect` on the
  same day reports the stored window and asks for `--force` rather than passing stale data off
  as the requested one.
- Items are **not capped** here: the request is free, so the stage keeps every candidate
  above the score floor. The cap belongs to the stages that are billed.
- Source failure: logged in the output file, the stage carries on. Replaying a window already
  collected changes nothing and spends nothing.

### 2. Enrichment — `vbj enrich [--date YYYY-MM-DD]`

- Input: `items.json`.
- Output: `data/<date>/enriched/<hash>.json` — body text extracted then truncated, plus the
  retained Hacker News comments.
- The article text is extracted cleanly (main content only: no menu, no cookie banner, no
  markup), then cut to roughly 2,000 estimated tokens. That truncation drives the cost of the
  next stage, since only the input is billed. An extraction yielding less than 200 tokens — a
  JavaScript shell, a status page, a social post — is treated as no text at all, and the item
  becomes metadata-only.
- The first Hacker News comments are kept: on that site the value often sits in the thread
  (an argued critique, missing context) rather than in the link.
- **Cache mandatory**, indexed by URL fingerprint. An article already enriched is never
  fetched twice.
- An item whose article is unreachable (paywall, error) stays in the batch with a "text
  unavailable" status: triage can then judge on the title and metadata alone.

### 3. Triage — `vbj triage [--date YYYY-MM-DD]`

- Input: `items.json` + `enriched/` + `config/questions.toml`.
- Output: `data/<date>/scores.json` — for each item, the typed answer to each question, with
  its distribution and confidence, then the aggregate score computed in code.
- Only items that were actually enriched are scored, and each one costs a single call: the state
  is that item, and all the questions about it travel together, ingested once.
- The confidence filter runs before the ranking: an item whose weakest confidence falls below
  the configured threshold is dropped without discussion.
- The grid is snapshotted into `scores.json` next to the scores, so a ranking can be traced back
  to the questions that produced it.
- Full detail in [TypeSafe triage](typesafe-triage.md).

### 4. Writing — `vbj write [--date YYYY-MM-DD]`

- Input: the N items kept by triage, with their full text.
- Output: `digest/<date>.md` — this is **the deliverable**.
- Expected format: for each kept item, the title **translated into French** as a link to the
  article, then the source, the English original title, the link, the category, the scores, a
  summary **in French** running from a few sentences up to about fifteen, and a "why this one"
  line. At the end, the list of dropped items with their score, to keep a trace of what was
  rejected and make the ranking contestable — those stay in English, being raw ranking data
  rather than prose.
- Writing happens **directly in French** from English sources, with no translation step.
- The digest **states the window it covers**, read back from `items.json`, so a rolling digest
  is never mistaken for a complete day — and a 23 or 25 hour civil day says so.
- **Written for a non-specialist.** Each item says what the thing is before saying why it
  matters, and unpacked jargon rather than repeated jargon. The digest is meant to be
  listened to later, so a sentence that must be read twice is a defect.

### 5. What comes next (V3, outside V1)

Two-voice script → local TTS → podcast RSS feed. The Markdown digest is the only input of
that stage: nothing in the pipeline above may need to change to add it.

## File tree

```text
veille-by-jev/                # repository root
  README.md
  docs/                       # scoping documentation
  pyproject.toml
  config/
    sources.toml          # enabled sources and their parameters
    questions.toml        # state, questions, weights, thresholds
    write.toml            # writer model, digest size, category labels
    write-prompt.md       # the writer's system prompt, including readability
  veille/
    cli.py                # collect / enrich / triage / write subcommands
    config.py             # TOML reading and window computation
    models.py             # contract between stages, deduplication rule
    sources/hn.py         # Hacker News collection
    enrich.py             # fetching, extraction, truncation, cache
    triage.py             # Jev call, score combination
    write.py              # digest assembly, from the model's prose
    clients/openrouter.py # writer adapter (OpenAI-compatible, replaceable)
    store.py              # data/ layout, fingerprints, seen.jsonl
    clients/typesafe.py   # Jev adapter (replaceable)
  tests/                  # pytest, never touches the network
  data/
    <date>/items.json
    <date>/enriched/<hash>.json
    <date>/scores.json
  digest/
    <date>.md
  seen.jsonl              # URLs already digested, append-only
```

No database in V1. Tracking "already seen" lives in an append-only `seen.jsonl`: readable,
greppable, diffable.

## Invariants

- **Idempotence.** Replaying a stage without `--force` does not rewrite an existing file and
  does not spend billed calls again.
- **Per-date resume.** Every stage takes a date; the night of the 12th can be replayed
  without touching anything else.
- **Partial failure tolerated.** An item in error does not bring the stage down.
- **Cost measured.** Every run of `triage` and `write` prints the number of input tokens
  consumed and the estimated cost when it finishes. A pipeline you cannot price becomes
  uncontrollable.
- **Rejection is traceable.** What was dropped and why stays written somewhere; without that,
  a grid that is too strict is indistinguishable from a quiet news day.

## Verification

At the end of V1, the following commands must run unattended and without error:

```bash
uv run vbj collect --date 2026-09-20
uv run vbj enrich  --date 2026-09-20
uv run vbj triage  --date 2026-09-20
uv run vbj write   --date 2026-09-20
```

Acceptance criterion: the fourth command produces a `digest/2026-09-20.md` that Stéphane
reads all the way through. That is the only judge that matters at this stage.
