# veille-by-jev

## Objective

Turn an endured technology watch (scrolling Hacker News, Reddit, arXiv) into a watch you
**listen to**: an agent collects, ranks and writes a nightly summary, in French, meant to
become a short two-voice audio episode later on.

Two goals, in this order:

1. **Learn curation** — collect widely, score, deduplicate, keep only what deserves attention.
2. **Learn the text → voice chain** — but only once the text deliverable is good.

## Guiding rule

> Voice is 10% of the project. Curation is 90%. If the digest is bad, the podcast will be bad.

Operational consequence: the first deliverable is a Markdown file that Stéphane reads and
judges in three minutes. TTS is added only after the digest's quality is validated.

## Scope

| Phase | Content | Status |
| --- | --- | --- |
| V1 | Hacker News → Markdown digest (collect, enrich, triage, write) | in progress — `collect` operational, three stages to go |
| V2 | Reddit over RSS, arXiv, cross-day deduplication | planned |
| V3 | Two-voice script, local TTS, podcast RSS feed | planned |

Explicitly out of scope: email or calendar notifications, voice cloning, web application,
user accounts, automatic publishing to a platform.

## Target architecture

```text
  sources                   pipeline (one command per stage, files as the interface)
  ┌──────────┐    ┌──────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
  │ HN       │───▶│ collect  │──▶│  enrich  │──▶│   triage   │──▶│  write   │──▶ digest.md
  │ (Algolia)│    │ items.json│   │ enriched/│   │ Jev + code │   │  (LLM)   │
  └──────────┘    └──────────┘   └──────────┘   └────────────┘   └──────────┘
  (V2) Reddit / arXiv                                          (V3) two-voice script → TTS → RSS
```

Triage is the only stage that makes a **judgement**; writing is the only stage that produces
**text**; collection and enrichment are deliberately dumb and verifiable.

## Navigation

- [Pipeline workflow](<docs/Workflow du pipeline.md>) — the five stages, their inputs/outputs and the invariants.
- [TypeSafe triage](<docs/Triage TypeSafe.md>) — state definition, typed questions, weights, costs.
- [Developer handoff](<docs/Handoff développeur.md>) — executable brief for the development agent.
- [Implementation journal](<docs/Journal de mise en œuvre.md>) — timeline, measurements, decisions, checklist.
- [Renaming the folder](<docs/Renommage du dossier.md>) — one-off procedure to align the local folder with the project name.

The notes above are written in French; this README is the English entry point.

## Decisions

| Decision | Rejected alternative | Reason |
| --- | --- | --- |
| Text deliverable first, voice later | Audio episode right away | A bad digest must be caught by reading, not by listening for 15 minutes |
| Output written in French from English sources | Translate, then read | One step fewer, no cascading loss of nuance |
| Triage engine = Jev (TypeSafe) | Classic generative LLM | Typed output with no generated text: no malformed JSON, no paid retries, free output |
| Questions and weights in a config file | Instructions inside a prompt | The grid becomes weighted and editable without rewriting a prompt |
| Replayable CLI stages, files as the interface | Long-running service or database | Replay triage without refetching, inspect intermediate state, diff two days |
| Python | Rust | Rust would buy a few milliseconds on a nightly job; the ecosystem (TypeSafe SDK, HTML extraction, TTS) is Python-first |
| Local TTS (Piper or Kokoro) in V3 | Cloud TTS API | No cloning needed: local is enough for French, with no data leaving the machine and no billing |
| `tts-serve` rejected | Use it as the engine layer | Its value is cloning and multiple engines; without cloning it is a needless dependency |
| Civil-day window in a configurable timezone | Rolling 24 h back from run time | The same `--date` always yields the same batch, whatever time the run happens; a nightly cron passing yesterday's date covers a whole Hacker News day |
| `items.json` keeps every candidate, uncapped | Truncate to the digest size at collection | Collection costs a single request, so the snapshot is free; the cap belongs to the stage that pays for it |
| English code, CLI and configuration; French digest only | French throughout | The repository is public; the digest is French because its reader is |

## Constraints and risks

- **Fragile sources.** Article scraping breaks regularly (paywalls, JS). Collection must tolerate per-item failure and keep going.
- **Costs under control, but volume-dependent.** Estimated in [TypeSafe triage](<docs/Triage TypeSafe.md>); only input is billed.
- **TypeSafe is a closed beta**, launched on 2026-09-15, so it sits behind an adapter that can be swapped for a local model.
- **Privacy.** V1 only handles public sources. No personal data (mail, calendar) enters the pipeline.
- **Unmeasured quality drift.** Without feedback, the digest can degrade unnoticed. To be addressed in V2 with a log of episodes read or skipped.

## Open questions

- How many items kept per digest: 5, 8, 12?
- How many candidates should triage score, now that collection keeps the whole day?
- Deduplication: is URL and domain enough, or is semantic grouping needed?
- Confidence threshold below which an item is dropped without discussion?
- Should HN comments be kept in the state, and where in the prompt?
- Which model writes the digest, and should it differ from the triage model?

## References

Facts verified on **2026-09-17**:

- Hacker News API (Algolia): `https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=N` → HTTP 200, fields `points`, `num_comments`, `title`, `url`, `created_at`.
- Hacker News API (Firebase): `https://hacker-news.firebaseio.com/v0/topstories.json` → HTTP 200, list of ids.
- Reddit RSS: `https://www.reddit.com/r/<sub>/top/.rss?t=day` → HTTP 200, unauthenticated; fields `title`, `id`, `author`, `updated`, `content` — **no score and no comment count**.
- Reddit JSON (`/top.json`): returns something other than JSON (blocked) — authentication required.
- arXiv: `https://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&sortOrder=descending&maxResults=N` → HTTP 200 over HTTPS (over plain HTTP, the response is unusable).
- French-language feeds tested: `https://www.lemonde.fr/rss/une.xml` → 200; `https://next.ink/feed/` → 200.
- TypeSafe / Jev: input at USD 0.042 per million tokens, **output free** (public announcement of 2026-09-15, read on 2026-09-17).
- Algolia pagination caps at 1000 hits per query: 1,139 stories were published on 2026-09-16 and 1,000 of them are reachable (measured 2026-09-17). The oldest stories of a day are therefore out of reach, which does not matter for a ranking that keeps the best scores.

## Status

**Phase:** V1 in progress — stage 1 of 4 (`collect`) is operational
**Last updated:** 2026-09-17

Stage 1 is implemented and exercised on a real Hacker News day (2026-09-16):

| Measurement | Value |
| --- | --- |
| Stories reachable in the window | 1,000 of the 1,139 published |
| Candidates kept above the score floor | 984 (423 KB written) |
| HTTP requests | 1, in about 0.9 s |
| Model calls, cost | 0, USD 0.00 |

`enrich`, `triage` and `write` are not implemented; no API key has been used yet, so the
TypeSafe cost figures above are still sizing hypotheses rather than measurements. The
project is developed one stage at a time, each stage verified against real data before the
next one starts; 44 tests cover stage 1 without touching the network.
