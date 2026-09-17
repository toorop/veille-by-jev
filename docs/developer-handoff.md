# Developer handoff

## Purpose of this document

A standalone, executable brief for implementing V1 of [veille-by-jev](../README.md).
Everything needed is stated here: nothing depends on an earlier conversation.

How this project is developed: Stéphane starts by writing a scoping document, then moves
forward in small, tested and validated steps. The steps must each be deliverable and
verifiable on their own, not one big final batch.

## Goal of V1

A Python CLI, runnable on the development machine, that produces a French
`digest/<date>.md` file every night from the day's Hacker News articles.

**Outside V1 scope**: Reddit, arXiv, audio summary, TTS, RSS feed, web interface, database,
notifications, deployment.

## Environment

- Linux, Python 3.11 or later, dependencies and virtual environment managed with `uv`.
- Machine: NVIDIA RTX 5080 GPU, 64 GB of RAM, Ryzen 9800X3D CPU. The GPU is unused in V1.
- TypeSafe API access (beta): key supplied by Stéphane through an environment variable. The
  key must never be written into a file of the repository.
- The code repository lives at the project root, at the same level as `docs/`. Only `docs/`
  holds documentation: everything else is code and generated data.

## Expected file tree

```text
veille-by-jev/                # repository root (= working directory)
  README.md                   # project index
  docs/                       # scoping documentation (this folder)
  pyproject.toml
  .gitignore
  .env.example                # variable names, never values
  config/
    sources.toml
    questions.toml
  veille/
    cli.py
    config.py                   # TOML reading and window computation
    models.py                   # contract between stages (Item, Window, deduplication)
    store.py
    sources/hn.py
    enrich.py
    triage.py
    write.py
    clients/typesafe.py
  tests/                        # pytest, no test touches the network
  data/<date>/{items.json,enriched/,scores.json}
  digest/<date>.md
  seen.jsonl
```

## Subcommands to deliver

| Command | Input | Output | Constraint |
| --- | --- | --- | --- |
| `vbj collect --date D` | `config/sources.toml` | `data/D/items.json` | no model call |
| `vbj enrich --date D` | `items.json` | `data/D/enriched/<hash>.json` | cache by URL fingerprint |
| `vbj triage --date D` | `items.json` + `enriched/` + `config/questions.toml` | `data/D/scores.json` | calls TypeSafe |
| `vbj write --date D` | the first N of `scores.json` | `digest/D.md` | calls a generative LLM |

Stage details and invariants: [Pipeline workflow](pipeline-workflow.md). State, questions and
costs: [TypeSafe triage](typesafe-triage.md).

## Dependencies

- HTTP client: `httpx`.
- Main text extraction from a page: `trafilatura`.
- RSS reading (V2, but prepare the abstraction): `feedparser`.
- Config and response validation: `pydantic`.
- CLI: `typer` or `argparse` — the implementer's choice, the essential point being that every
  subcommand is documented by `--help`. `typer` was chosen.
- Official TypeSafe SDK if one is published; otherwise a direct HTTP call wrapped in
  `clients/typesafe.py`.
- Writing: a generative LLM, called through an equally wrapped interface.
- Development only: `pytest` for tests, `ruff` for formatting and linting, both in the `dev`
  group and configured in `pyproject.toml`.

## Configuration files

`config/sources.toml` — enabled sources, time window, score floor. **No item cap here**:
collection costs a single request and keeps every candidate, so the cap lives where it is
billed, that is in `enrich`, then in `triage`.

`config/questions.toml` — definition of the state, typed questions, descriptive scale of each
`Score`, confidence threshold, coefficients of the aggregation formula. **This is the file
Stéphane will edit to iterate on triage quality.** It must be readable and commented, and
changing it must never require touching code.

## Digest output contract

`digest/<date>.md`, in Markdown:

- a dated title;
- for each kept item: original title, source, link, two to four sentences of summary **in
  French**, and a "why this one" line;
- at the end, a section of the dropped items with their score, to make the ranking
  contestable;
- at the end, the cost of the run: number of input tokens and estimated cost.

That file is the only planned input of the V3 audio stage: its structure must stay stable.

## Definition of done for V1

- The four commands chain together without manual intervention for a given date.
- Replaying `collect` or `enrich` does not redo finished work and does not spend credit again.
- An item whose article is unreachable does not fail the run.
- The digest is produced in French, with links and sources, and Stéphane reads it all the way
  through: that is the only success criterion that matters at this stage.
- Every command prints the cost it generated when it finishes.

## Suggested working order

**Execution rule: stop at the end of each numbered stage**, show the real command output and
wait for Stéphane's validation before moving to the next one. Never chain the six stages in a
single batch.

1. ✅ `collect` alone, until `items.json` is correct on a real day (done 2026-09-17).
2. ✅ `enrich` with cache, checking that roughly 2,000 tokens of text are extracted from a real
   article, and that an empty page is marked unavailable rather than passed on as an article
   (done 2026-09-17).
3. ✅ `triage` with **a single** question, to validate the call contract and the answer format
   (done 2026-09-17: five real items on 2026-09-16, 12,016 input tokens, USD 0.000505).
4. Add the other questions and the weighted aggregation.
5. `write` and the first real read of the digest.
6. Only after that: tuning the grid, adding sources.

## Known pitfalls

- **Hacker News.** The `front_page` endpoint alone makes collection depend on the hour of the
  run. Implemented instead: a civil-day window (configurable timezone) queried through
  `created_at_i`, then a local ranking by points. Endpoints verified on 2026-09-17:
  `hn.algolia.com/api/v1/search` and `hacker-news.firebaseio.com/v0/topstories.json`. Algolia
  caps pagination at 1,000 hits per query, so the oldest stories of a busy day are out of
  reach — harmless when only the best scores are kept.
- **Reddit** (V2 only). The RSS feed `reddit.com/r/<sub>/top/.rss?t=day` works without
  authentication but provides **neither score nor comment count**; the `.json` endpoint is
  blocked. Do not build a ranking on a missing signal.
- **arXiv** (V2). Use HTTPS: over plain HTTP the response is unusable.
- **Text extraction.** Expect failure: paywalls, JS pages. An item without text must remain
  rankable on its metadata alone.
- **Cost.** Only the input is billed by the ranker: truncating the state is the main cost
  lever. Never send whole articles.
- **Secrets.** API key in an environment variable only, `.env` ignored by git, `.env.example`
  without values.
- **What is not measured must not be asserted.** The volumes and costs in
  [TypeSafe triage](typesafe-triage.md) are sizing hypotheses: the first run must replace them
  with real measurements.
