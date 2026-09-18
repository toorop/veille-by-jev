# veille-by-jev

**A nightly technology watch, triaged by a decision model and written up as a French digest.**

Every night, `veille-by-jev` pulls the day's Hacker News stories, fetches and extracts the
articles, scores them against a grid of typed questions, and writes a Markdown digest in French.
The point is the filter, not the volume: 200 candidates become a dozen or so things worth
reading, each summarised in plain French, with the ones that were set aside still listed and
scored so the ranking can be contested.

```text
  sources                    pipeline — one command per stage, files as the interface
  ┌──────────┐    ┌──────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
  │ HN       │───▶│ collect  │──▶│  enrich  │──▶│   triage   │──▶│  write   │──▶ digest.md
  │ (Algolia)│    │ items.json│   │ enriched/│   │ Jev + code │   │  (LLM)   │
  └──────────┘    └──────────┘   └──────────┘   └────────────┘   └──────────┘
  (V2) Reddit / arXiv                                          (V3) two-voice script → TTS → RSS
```

Triage is the only stage that makes a **judgement**; writing is the only stage that produces
**text**; collection and enrichment are deliberately dumb and verifiable.

## Why this exists

Reading Hacker News every day is a chore, and the interesting part is not the reading: it is
deciding what deserves attention.

> Voice is 10% of the project. Curation is 90%. If the digest is bad, the podcast will be bad.

So the first deliverable is a Markdown file you read in three minutes, and a text-to-speech
stage is planned only once the text is worth listening to. Two goals, in that order: learn
curation, then learn the text-to-voice chain.

## Requirements

- Linux, **Python 3.11 or later**, and [uv](https://docs.astral.sh/uv/) for the environment.
- A **TypeSafe (Jev)** key for the triage, from <https://console.typesafe.ai/settings/keys>.
  Jev is a closed beta launched on 2026-09-15; it evaluates typed questions without generating
  text, which is why the ranking is cheap and cannot produce malformed JSON.
- An **OpenRouter** key for the writing, from <https://openrouter.ai/keys>. Any
  OpenAI-compatible endpoint works instead: the base URL is a line of `config/write.toml`.

The GPU is not used, and nothing else is installed globally.

## Install

```bash
git clone https://github.com/toorop/veille-by-jev.git
cd veille-by-jev
cp .env.example .env     # then fill in the two keys
uv sync
uv run vbj --version
```

`.env` is ignored by git and read at startup by the pipeline itself. Only secrets belong there:
the writer model, its endpoint and the digest quota live in `config/`. A variable already
exported in your shell wins over the file, so `TYPESAFE_LOG_LEVEL=DEBUG uv run vbj triage …`
overrides it for one run.

## Use

Four commands, one per stage. Files are the interface between them, so any stage can be replayed
alone.

| Command | Reads | Writes | Calls a model |
| --- | --- | --- | --- |
| `vbj collect [--date D]` | `config/sources.toml` | `data/D/items.json` | no |
| `vbj enrich [--date D]` | `items.json` | `data/D/enriched/<hash>.json` | no |
| `vbj triage [--date D]` | `items.json`, `enriched/`, `config/questions.toml` | `data/D/scores.json` | Jev |
| `vbj write [--date D]` | the admitted items of `scores.json` | `digest/D.md` | OpenRouter |

A full night, as run for 2026-09-16:

```bash
uv run vbj collect --date 2026-09-16   # 1,000 stories, 984 candidates, 1 request, 0.9 s, free
uv run vbj enrich  --date 2026-09-16   # 200 articles fetched, 166 with usable text, 2 min 31 s
uv run vbj triage  --date 2026-09-16   # 200 items scored, 14 clear the floor, 88 s, USD 0.0167
uv run vbj write   --date 2026-09-16   # 14 items written up, 26 s, USD 0.0198
```

### Two ways to cut the window

**`--date D` is the whole civil day D**, in the configured timezone. The same date always yields
the same batch, whatever time the run happens: that is what makes a night replayable and two
writers comparable on the same state.

**Without `--date`, the window is the last `window_hours` ending now** (24 h by default), labelled
by the civil day it ends in. The digest then never lags, which is the point of a watch:

```text
$ uv run vbj collect
vbj collect (last 24 h, no --date)
  timezone   : Europe/Paris — UTC window 2026-09-17T06:22:25Z → 2026-09-18T06:22:25Z
  hn         : ok — 1000 raw items, 988 kept by the points >= 1 filter, 1 request(s), 0.97 s
  candidates : 988 after filter → 988 after deduplication, all written
  written    : data/2026-09-18/items.json (423677 bytes)
  published  : 2026-09-17T06:30:13Z → 2026-09-18T06:16:44Z
  cost       : USD 0.00 — no model call at this stage
```

The newest story in that batch was published five minutes before the run, where a civil day would
have ended at midnight. The price is that the same label no longer means the same batch, so a
second `collect` on the same day asks for `--force` rather than pretending the stored file is the
window you asked for:

```text
$ uv run vbj collect
data/2026-09-18/items.json already exists — nothing to do.
  items      : 988 (collected at 2026-09-18T06:22:26Z)
  stored     : 2026-09-17T06:22:25Z → 2026-09-18T06:22:25Z (24.0 h)
  wanted     : 2026-09-17T06:22:29Z → 2026-09-18T06:22:29Z (24 h), which differs from the stored one
  re-run     : add --force to collect again
```

A run that crosses midnight between two stages needs an explicit `--date`, since the default
otherwise means "the run happening now". Every digest states the window it covers, so a rolling
digest is never mistaken for a complete day.

Each command prints what it did, and what it cost. The last one:

```text
$ uv run vbj write --date 2026-09-16
vbj write --date 2026-09-16
  selection  : 14 written up, 186 set aside, out of 200 triaged (floor 2.0)
  model      : google/gemini-2.5-flash — 31915 input, 4077 output, 25.51 s
  cost       : USD 0.019767
  written    : digest/2026-09-16.md (51025 bytes)
```

**Replaying is safe and free.** A stage whose window was already collected does nothing and
spends nothing; `--force` makes it run again. `enrich` and `triage` also take `--limit N` to try
a handful of items, and `write` takes `--model` and `--output` to compare two writers on the same
night without overwriting the digest. `write --dump-prompt <path>` writes the exact state it would
send and stops, without reading a key or calling anything — see
[Testing a writer outside the pipeline](docs/writer-testing.md).

### What the digest looks like

The head of the digest, from the 18 September rolling run — a real one, abridged in one place:

```markdown
# Veille du 18 septembre 2026

**14 items retenus** sur 200 candidats triés. Seuil d'admission : 2,0.

Fenêtre couverte : du 17 septembre 2026 08 h 22 au 18 septembre 2026 08 h 22 (Europe/Paris, 24 h).

## [LLM à paramètres infinis : générer et adapter les poids à partir de données en direct](<https://arxiv.org/abs/2609.18842>)

- **Source** : Hacker News — 133 points, 38 commentaires
- **Titre original** : Infinite-Parameter LLMs: Generating and Adapting Weights from Live Data
- **Lien** : <https://arxiv.org/abs/2609.18842>
- **Catégorie** : recherche
- **Scores** : interest 2,6 · density 3,9 · primary_source 0,8 · agrégat 2,47

Cet article propose une nouvelle architecture, appelée "LLM à paramètres infinis", qui permet au
modèle d'apprendre en continu à partir des interactions en direct. Inspirée des architectures
"Mixture-of-Experts" (MoE), cette approche utilise un "hyperréseau" compact. Un hyperréseau est un
réseau neuronal qui génère les poids (les valeurs numériques qui déterminent le comportement du
modèle) d'un autre réseau neuronal. […] Cela signifie que les connaissances et les comportements
appris persistent au-delà d'une seule interaction, libèrent la fenêtre de contexte (la quantité
d'informations que le modèle peut traiter à la fois) et peuvent mieux généraliser que
l'apprentissage contextuel traditionnel.

**Pourquoi celui-là.** Cette recherche propose une solution pour que les LLM apprennent et
s'adaptent en temps réel aux interactions des utilisateurs, ce qui pourrait rendre les assistants
IA plus pertinents et persistants sans nécessiter de réentraînement coûteux ou de prompts
surchargés.
```

The digest is written for a reader who is interested in the field but **not a specialist**: an
item says what the thing is before saying why it matters, and jargon is unpacked rather than
repeated. That requirement lives in `config/write-prompt.md`, not in the code, so it can be
tightened without touching anything else.

Each kept item carries **its title translated into French as a link**, then the source, the
English original title, the link, the category, the scores behind the decision, a French summary
running from a few sentences up to about fifteen, and a "why this one" line. The kept
items are followed by the ones that were set aside, with their score, their category and
their title as a link to the article, so a rejection can be checked in one click — 186 rows on
that night — and by the cost of the run.

The admission floor and the quota do different jobs. **The floor decides**: an item below it
never appears, however few items clear it, so a quiet night gives a short digest. The quota only
bounds the volume on a rich night. On 2026-09-16, 14 items cleared the floor of 2.0 and the
quota of 15 never came into play.

## Cost

Measured on the 2026-09-16 batch, not estimated:

| Stage | Per night | Per month |
| --- | --- | --- |
| `collect` | free | free |
| `enrich` (718 HTTP requests, 2.5 min) | free | free |
| `triage` — Jev, 200 items, input only | USD 0.0167 | USD 0.50 |
| `write` — Gemini 2.5 Flash | USD 0.0198 | USD 0.59 |
| **total** | **USD 0.04** | **USD 1.09** |

The writer and the triage now cost about the same. The cost follows the number of items admitted,
so it varies with the night rather than being fixed by the quota: swapping the writer is one line
of `config/write.toml`, where the candidate models and their measured cost are listed — the
cheapest brings the writer down to USD 0.06 per month.

Costs are printed by the stages themselves and measured by the provider, never estimated:
OpenRouter reports the real cost of the call, and Jev reports the input tokens it billed.

## Configuration

No code has to change to retune the watch:

| File | What it holds |
| --- | --- |
| `config/sources.toml` | enabled sources, the rolling window length and its timezone, the score floor, the enrichment budget |
| `config/questions.toml` | the grid: typed questions, their descriptive levels, the admission floor, the weights |
| `config/write.toml` | the writer model, the digest quota, its temperature and output ceiling |
| `config/write-prompt.md` | the writer's system prompt, including the readability requirement |

**The grid is the file that decides what the digest keeps.** A question is a `score` (an ordered
spectrum, level by level), a `choice` (a label), or a `noul` (a yes/no as a probability):

```toml
[questions.interest]
type = "score"
instructions = "Interest for a French-speaking developer who follows applied AI and local tooling."
criteria = [
  "No interest: marketing, fundraising, or a topic outside the field",
  # … level 2, level 3 …
  "Essential: a major release, a foundational paper, or a shift in the field",
]

[aggregation.weights]
interest = 0.5
density = 0.3
primary_source = 0.2
```

An item is ranked on `score − z × spread`, a lower bound on its position rather than the position
itself: hesitating between two neighbouring levels costs little, hesitating between "no interest"
and "essential" costs a lot. A question left out of the weights is recorded but does not rank —
that is how the category stays a label rather than a quality signal. A weight naming an unknown
question is refused when the file loads, because a typo there would silently drop a question
from the ranking.

Every choice is recorded in `scores.json` next to the raw answers and the grid that produced
them, so a ranking can be recomputed — or contested — without calling the engine again.

## How it is built

Four invariants hold the pipeline together:

- **Files are the interface.** No service, no database, no state in memory between runs. You can
  replay the triage after editing the grid, inspect any intermediate file, or diff two days.
- **Replaying costs nothing.** Without `--force`, a stage whose output exists does nothing at
  all. That matters most at the paid stages.
- **The model writes prose, never figures.** The dated title, the links, the sources, the scores,
  the set-aside table and the cost line are generated from the data, so the writer can neither
  misquote a figure nor forget a link. It answers in JSON keyed by item id, and a malformed
  answer becomes a reserve written into the digest instead of a broken file.
- **Failure is a state, not an exception.** An article that cannot be fetched or extracted
  becomes a metadata-only item and the stage carries on: 34 of the 200 candidates on that night.
  A source that fails is reported in the output file, not thrown.

## Development

```bash
uv run pytest          # 156 tests
uv run ruff check .    # lint
uv run ruff format .   # formatting
```

The HTTP layer is monkeypatched and the engines are injected, so the suite runs in under a second,
needs no keys, and touches neither the network nor a provider. Adding a question to the grid needs
no test change; adding a question *type* does.

## Project layout

```text
veille-by-jev/
  README.md
  docs/                       # scoping notes, in French
  pyproject.toml
  .env.example                # variable names, never values
  config/
    sources.toml              # sources, window length, timezone, score floor
    questions.toml            # the grid: questions, levels, floor, weights
    write.toml                # writer model, digest quota, labels
    write-prompt.md           # writer system prompt, including readability
  veille/
    cli.py                    # collect / enrich / triage / write
    config.py                 # TOML reading, window computation
    models.py                 # contract between stages, deduplication rule
    store.py                  # data/ layout, fingerprints, atomic writes
    sources/hn.py             # Hacker News collection
    enrich.py                 # fetching, extraction, truncation, comments, cache
    triage.py                 # state building, ranking, confidence
    write.py                  # digest assembly from the model's prose
    clients/typesafe.py       # Jev adapter (replaceable)
    clients/openrouter.py     # writer adapter (OpenAI-compatible, replaceable)
  tests/
  data/<date>/                # generated, ignored by git
  digest/<date>.md            # generated, ignored by git
```

## Status and limits

**V1 is complete**: the four stages run end to end, a first digest exists
(`digest/2026-09-16.md`), and it has been read in full and accepted. What comes after the digest
is undecided: the pipeline currently stops there.

Honest limits, all measured rather than assumed:

- **The engine is not deterministic.** Two runs over the same five items moved scores by up to
  0.05, so `--force` does not reproduce a ranking exactly. Idempotence protects the file and the
  money, not the bit-for-bit reproducibility of a judgement.
- **Coverage is partial.** Algolia caps pagination at 1,000 hits: 1,139 stories were published on
  2026-09-16 and 1,000 were reachable. The oldest of a busy day stay out of reach, which does not
  matter for a ranking that keeps the best scores.
- **Extraction fails on 17% of articles** (JavaScript shells, status pages, social posts). Those
  items are judged on their title and metadata alone, and the digest says so.
- **Every figure comes from one night.** A weekday, a weekend and a news-heavy day would make the
  costs and timings solid.
- **The token estimate used to bound the state** understates the triage invoice by a measured
  factor of 1.22.

## Roadmap

| Phase | Content | Status |
| --- | --- | --- |
| V1 | Hacker News → Markdown digest (`collect`, `enrich`, `triage`, `write`) | done |
| V2 | Reddit over RSS, arXiv, cross-day deduplication, feedback on what was read | planned |
| V3 | Two-voice script, local TTS, podcast RSS feed | planned |

Explicitly out of scope: email or calendar notifications, voice cloning, web application, user
accounts, automatic publishing to a platform.

## Decisions worth knowing

| Decision | Rejected alternative | Reason |
| --- | --- | --- |
| Text deliverable first, voice later | Audio episode right away | A bad digest is caught by reading, not by listening for 15 minutes |
| Written in French from English sources | Translate, then read | One step fewer, no cascading loss of nuance |
| Jev, a decision model, for the triage | A generative LLM | Typed answers by construction: no malformed JSON, no paid retries, output free |
| Lower-bound ranking, `score − z × spread` | Filter on the confidence scalar | Measured: filtering on confidence alone ranked backwards, discarding the day's best-scored story because the engine hesitated between two high, neighbouring levels |
| Files as the interface, replayable stages | A service, or a database | Replay the triage without refetching, inspect the intermediate state, diff two days |
| English code, CLI and configuration | French throughout | The repository is public; French is reserved for the digest, the only thing a human reads |

The full decision log, including the four sizing hypotheses that measurement refuted, is in
[the journal](docs/implementation-journal.md).

## Documentation

The scoping notes in `docs/` are in French, kept as the project's working memory — what was
decided, what was measured, and what was wrong:

- [Pipeline workflow](docs/pipeline-workflow.md) — the stages, their inputs and outputs, the invariants.
- [TypeSafe triage](docs/typesafe-triage.md) — the state, the typed questions, the weights, the measured costs.
- [Developer handoff](docs/developer-handoff.md) — the executable brief the project was built from.
- [Implementation journal](docs/implementation-journal.md) — timeline, measurements, decisions, checklist.
- [Testing a writer outside the pipeline](docs/writer-testing.md) — how to dump the exact user
  prompt and compare models elsewhere, and what changes when the digest grows to 20 items. The
  prompt sent for 2026-09-16 is committed as
  [`examples/2026-09-16/write-user-prompt.json`](examples/2026-09-16/write-user-prompt.json), to be
  pasted into a model comparison tool alongside `config/write-prompt.md`.
- [Renaming the folder](docs/renaming-the-folder.md) — one-off local procedure.
- [Original development brief](docs/prompt.md) — the prompt the project was started from.

## References

Endpoints and facts verified on **2026-09-17**:

- Hacker News API (Algolia): `https://hn.algolia.com/api/v1/search` → HTTP 200, fields `points`,
  `num_comments`, `title`, `url`, `created_at_i`, `_tags`; pagination caps at 1,000 hits.
- Hacker News API (Firebase): `https://hacker-news.firebaseio.com/v0/item/<id>.json` → HTTP 200,
  gives a story's `kids` and each comment's `text`.
- Reddit RSS: `https://www.reddit.com/r/<sub>/top/.rss?t=day` → HTTP 200, unauthenticated, but
  **neither score nor comment count**; the `.json` endpoint is blocked.
- arXiv: `https://export.arxiv.org/api/query?search_query=cat:cs.CL&…` → HTTP 200 over HTTPS
  (over plain HTTP the response is unusable).
- TypeSafe / Jev: input at USD 0.042 per million tokens, **output free**.
- OpenRouter: `usage.cost` in the chat-completions response carries the real cost of the call,
  as documented in its OpenAPI specification.
