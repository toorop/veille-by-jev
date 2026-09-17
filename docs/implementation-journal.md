---
title: Implementation journal
aliases:
  - Implementation journal
type: journal
status: V1 in progress — collection operational
created: 2026-09-17
updated: 2026-09-17
tags:
  - technology-watch
  - journal
  - decisions
---

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

## Measurements

Sizing hypotheses are replaced by readings as they come. Rows without a measurement belong to
stages not yet written.

| Quantity | Hypothesis | Measured | Date |
| --- | --- | --- | --- |
| Items collected per night | 200 | 984 candidates kept out of 1,000 received | 2026-09-17 |
| HTTP requests for collection | not estimated | 1 | 2026-09-17 |
| Duration of collection | not estimated | 0.9 s | 2026-09-17 |
| State tokens per item | 1,500 | — | — |
| Triage cost per month | USD 0.38 | — | — |
| Writing cost per month | not quantified | — | — |
| Total duration of the nightly run | not estimated | — | — |

## Checklist

- [x] `collect` operational on a real day
- [ ] `enrich` operational, with cache and failure tolerance
- [ ] `triage` operational with a single question
- [ ] full grid and coefficients in `config/questions.toml`
- [ ] `write` operational, digest in French
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
