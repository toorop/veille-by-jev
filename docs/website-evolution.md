# Publishing the digest: the open question

**Status: an idea, recorded on 2026-09-18, not a plan.** Nothing in the code depends on it, and
nothing here has been built. It exists so the thinking is not lost and does not have to be redone.

The pipeline is finished: it produces `digest/<date>.md` every night. What happens to that file is
a separate decision, and this note lays out the options as they stand.

## What the digest would become

The reader of a website is not the reader of a file, so the shape changes before the technology
does.

- **One page per night is a hub, not a unit of search.** Fourteen unrelated subjects on one URL
  rank for nothing.
- **One page per article is what ranks**: a French title, a French summary, the source link, a
  category. The translated titles and the dated links this pipeline now produces are exactly the
  material such a page needs — the feature was a prerequisite, not a detail.
- **RSS is the real replacement for the podcast idea.** A watch is consumed by being notified, not
  by being visited. The feed costs almost nothing once the content is structured.
- The podcast is not dead, it is displaced: the natural audio unit is the article page, not the
  whole digest.

Two consequences that belong to the writer, not to the website:

- the system prompt says the digest is written *for one reader*: a French-speaking developer. A
  public site changes that reader, and the prompt has to say which one it is aimed at;
- the set-aside table — 186 rows on 2026-09-16 — is valuable for contesting the ranking and is
  noise on a public page. It belongs on its own page, or nowhere.

## Track A: all-Cloudflare, with EmDash

[EmDash](https://github.com/emdash-cms/emdash) is a CMS built on Astro and Cloudflare, from the
team Astro joined in 2025 ([announcement](https://blog.cloudflare.com/astro-joins-cloudflare/)).
It runs on D1 + R2 + Workers, or on any Node server with SQLite, and is in **beta preview**.

The property that matters here: content lives in the database and is rendered when the page is
built, so **a new entry appears without rebuilding the site**
([docs](https://docs.emdashcms.com/)). The current habit — push to GitHub, let Cloudflare rebuild —
still applies, but only to the *code and the theme*. The nightly content stops needing a deploy,
which removes the worst failure mode of the naive design: a deploy step that fails *after* the
digest has been paid for.

### Shape

```text
Cron Trigger (UTC)
  └─ Worker: scheduled handler starts a container
       └─ the container runs the CLI: collect, enrich, triage, write
            └─ publishes the night to EmDash over its REST API
                 └─ Astro + EmDash (D1 + R2 + Workers) serves the pages, RSS and search
      R2 keeps the pipeline's working state: items.json, enriched/, scores.json
```

The pieces:

| Piece | Role |
| --- | --- |
| Cron Trigger → container | documented pattern, with its own example ([Cron Container](https://developers.cloudflare.com/containers/examples/cron/)) |
| Container image | the CLI as it is; `lite` or `basic` is enough (image size is bounded by the instance disk: 2 or 4 GB) |
| R2 | the working state, and the raw archive. An [R2 FUSE mount](https://developers.cloudflare.com/containers/examples/r2-fuse-mount/) could even leave `store.py` untouched |
| EmDash (D1 + R2) | the published content: an `editions` collection and an `articles` collection |
| Taxonomies | the `category` of the triage grid, which already exists |
| Secrets | `TYPESAFE_API_KEY` and `WRITE_LLM_API_KEY`, passed to the container as environment variables |

### Cost, against measurements

The nightly run takes about six minutes. On a `basic` instance (¼ vCPU, 1 GiB, 4 GB disk), that is
0.1 GiB-hours, 1.5 vCPU-minutes and 0.4 GB-hours per night — against the 25 GiB-hours, 375
vCPU-minutes and 200 GB-hours included in the $5 Workers Paid plan each month
([pricing](https://developers.cloudflare.com/containers/platform/pricing/),
[limits](https://developers.cloudflare.com/containers/platform/limits/)). The container therefore
adds nothing to the $5, and the models cost about $1 a month on top.

### Publishing is a write, not a deploy

EmDash's CLI exposes everything needed programmatically ([CLI reference](https://docs.emdashcms.com/reference/cli/)):
`content create <collection> --data`, `schema add-field --type portableText`, `taxonomy add-term`,
`content update` which requires the `_rev` token seen on a prior read, and `export-seed` for
getting the data out. Two notes:

- the CLI is Node, but it talks to a REST API. This pipeline is Python: it should call that API
  directly with `httpx` and a token, rather than shipping Node inside the image;
- `content create` auto-publishes unless `--draft` is passed, which gives a review step for free
  if it is ever wanted.

### What the pipeline would have to gain

1. **Persist the model's prose as JSON.** Today it exists only inside the Markdown digest, and the
   raw answer is written to disk only when parsing fails. Article pages need the fields.
2. **A publish step**, idempotent by construction: the dated slug (`2026-09-18`) is the key, so a
   re-run checks for the edition before creating it — the same reasoning as `collect` checking its
   stored window.
3. **Never publish an empty edition.** A night where nothing clears the floor already fails in
   `collect`; that must not become a page.

### Honest reservations

- **Beta preview.** The real risk: building an autonomous nightly pipeline on a young project. The
  data is in D1 and exportable, so the worst case is a missing entry, not lost work.
- **A small part of it is used.** The admin, revisions, passkeys, media library and sandboxed
  plugins are for humans. What is wanted is the schema, typed queries, taxonomies, search and RSS.
- **The site becomes dynamic.** Better for this use — no build minutes, instant publication,
  full-text search — but caching has to be handled deliberately for search engines.
- **Publication is a new failure point**, after a paid digest. It is replayable at no cost, since
  the digest already exists on disk.
- **Astro is no longer the build target of the content**, only of the theme. That is a change of
  mental model, not of stack.

## Track B: plain Astro content collections

The pipeline commits one Markdown file per night, Cloudflare rebuilds, exactly as today.

- **For**: no new dependency, no beta, the repository is the archive, and the existing habit works.
- **Against**: RSS, search, category archives and article pages are all written by hand; the site
  rebuilds every night; and a build failure after a paid digest is the failure mode Track A
  removes.

## Open questions

1. Who is the reader: the one the prompt describes, or a public?
2. One page per night as a hub plus one page per article, or the night only?
3. Where do the 186 set-aside rows go?
4. Publish automatically, or leave the night as a draft for a quick read first?
5. Publication through EmDash, or static files and a rebuild?

Nothing should be built before questions 1 and 5 are answered: they decide everything else.

## What the environment already says

Cloudflare now steers new projects towards Workers with static assets rather than Pages
([migration guide](https://developers.cloudflare.com/workers/static-assets/migrate-from-pages/)),
so "Cloudflare Pages" is no longer the default answer for the hosting half either. And Cron
Triggers fire in UTC: a trigger set for 08:00 Paris drifts by an hour twice a year. The rolling
window removes the problem entirely, since it always covers the last 24 hours whatever the hour —
one more reason it was the right default.
