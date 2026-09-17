"""Hacker News collection through the Algolia API.

Design choices:

- **Civil-day window, not "the last 24 hours".** The window comes from
  `config.day_window`: `--date 2026-09-16` covers 16 September from 00:00 to 24:00
  local time, whenever the run happens. That is what makes collection
  reproducible.
- **Ranking by points, not `front_page`.** `front_page` only holds what sits on the
  home page at run time, so the batch would depend on the hour. Here we query
  everything published inside the window, then keep the best scores.
- **Score filter only, no final truncation.** The source drops items below
  `min_points` and returns the rest sorted by score; truncation to `target_items`
  happens once, at pipeline level, so that it stays correct once several sources
  coexist.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from veille.config import CollectConfig, SourceSettings
from veille.models import CollectOutcome, Item, SourceReport, Window

# Coverage note: the Algolia API caps pagination at `MAX_HITS_PER_QUERY` results
# per request (see `veille.config`), so the oldest items of the window are never
# seen. On an ordinary Hacker News day (about 1,100 stories on 2026-09-16) coverage
# stays close to complete, and only the best scores are kept anyway.

# Priority order: a Show HN carries both the `story` and the `show_hn` tags, and
# the most specific tag is the one that describes the item.
_STORY_KINDS = ("show_hn", "ask_hn", "job", "story")


def _kind_from_tags(tags: list[str]) -> str:
    """Return the most specific Hacker News kind present in `tags`, else `story`."""
    present = set(tags)
    for kind in _STORY_KINDS:
        if kind in present:
            return kind
    return "story"


def _item_from_hit(hit: dict[str, Any]) -> Item | None:
    """Convert one Algolia hit into an `Item`, or `None` when it is unusable."""
    object_id = hit.get("objectID")
    created_at_i = hit.get("created_at_i")
    if not object_id or created_at_i is None:
        return None

    discussion_url = f"https://news.ycombinator.com/item?id={object_id}"
    title = (hit.get("title") or "").strip() or "(sans titre)"
    return Item(
        id=f"hn:{object_id}",
        source="hn",
        title=title,
        url=(hit.get("url") or "").strip() or discussion_url,
        discussion_url=discussion_url,
        author=hit.get("author"),
        points=int(hit.get("points") or 0),
        num_comments=int(hit.get("num_comments") or 0),
        published_at=datetime.fromtimestamp(int(created_at_i), tz=UTC),
        kind=_kind_from_tags(list(hit.get("_tags") or [])),
    )


def _fetch_page(
    client: httpx.Client, cfg: SourceSettings, window: Window, page: int
) -> dict[str, Any]:
    """Fetch one page of stories published inside `window`."""
    params = {
        "tags": cfg.tags,
        "numericFilters": (
            f"created_at_i>={int(window.start.timestamp())},"
            f"created_at_i<{int(window.end.timestamp())}"
        ),
        "hitsPerPage": cfg.hits_per_page,
        "page": page,
    }
    response = client.get(cfg.endpoint, params=params)
    response.raise_for_status()
    return response.json()


def collect(
    window: Window,
    cfg: SourceSettings,
    collect_cfg: CollectConfig,
    source_name: str = "hn",
) -> CollectOutcome:
    """Collect the best Hacker News items published inside `window`.

    Args:
        window: Half-open UTC window to cover.
        cfg: Settings of this source.
        collect_cfg: Pipeline-wide collection settings, used for the score floor.
        source_name: Name used in the report and in the item identifiers.

    Returns:
        The kept items and the execution report. This function never raises: a
        network or payload failure is reported through an error status instead.

    """
    started = time.monotonic()
    label = cfg.label or source_name
    report = SourceReport(source=source_name, label=label, status="error")
    hits: list[dict[str, Any]] = []

    try:
        with httpx.Client(
            timeout=cfg.timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "veille-by-jev/0.1 (collecte personnelle Hacker News)"},
        ) as client:
            for page in range(cfg.max_pages):
                payload = _fetch_page(client, cfg, window, page)
                report.requests += 1
                page_hits = payload.get("hits") or []
                hits.extend(page_hits)
                # Stop on a short page, or on the last page the API announces:
                # otherwise a full batch of 1000 hits triggers an empty request.
                is_last_page = page + 1 >= int(payload.get("nbPages") or 1)
                if len(page_hits) < cfg.hits_per_page or is_last_page:
                    break
    except (httpx.HTTPError, ValueError) as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        report.duration_s = round(time.monotonic() - started, 2)
        return CollectOutcome(items=[], report=report)

    items = [item for item in (_item_from_hit(hit) for hit in hits) if item is not None]
    report.fetched = len(items)

    # The same article can be posted twice: keep the entry with the best score.
    best_by_url: dict[str, Item] = {}
    for item in items:
        current = best_by_url.get(item.url)
        if current is None or item.outranks(current):
            best_by_url[item.url] = item

    retained = [item for item in best_by_url.values() if item.points >= collect_cfg.min_points]
    retained.sort(
        key=lambda item: (item.points, item.num_comments, item.published_at), reverse=True
    )

    report.status = "ok"
    report.kept = len(retained)
    report.duration_s = round(time.monotonic() - started, 2)
    return CollectOutcome(items=retained, report=report)
