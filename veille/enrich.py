"""Article fetching, main-text extraction, truncation and comment collection.

Design choices:

- **Cache by URL fingerprint, scoped to the day.** The enriched file holds only what the
  article said, never source metadata: a score that drifts over time must not invalidate
  the cache.
- **Truncation is the cost lever.** Only the input of the triage model is billed, so the
  body text is cut to an estimated token budget, and the cut is moved to a sentence
  boundary when one is close enough to the budget.
- **Failure is a state, not an exception.** An article that cannot be fetched or extracted
  produces a file with status `unavailable`: the item stays in the batch and triage can
  judge it on its metadata alone.
- **Comments come from the Firebase API**, one request per comment, in the order the API
  returns `kids`. That order is insertion order: close to chronological, but not sorted by
  time. A failed thread is recorded in `comments_error` rather than retried, because the
  article text is worth caching on its own.
"""

from __future__ import annotations

import html as html_module
import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any

import httpx
import trafilatura

from veille.config import EnrichConfig
from veille.models import Comment, EnrichedItem, EnrichReport, Item
from veille.store import enriched_path, read_json, url_fingerprint, write_json

HN_ITEM_API = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
USER_AGENT = "veille-by-jev/0.1 (collecte personnelle Hacker News)"

# Markers used to move the truncation cut back to a sentence boundary. A marker is only
# used when it does not throw away more than a fifth of the token budget.
_SENTENCE_MARKERS = (". ", "! ", "? ", ".\n", "\n\n")
_MIN_KEPT_RATIO = 0.8


def estimate_tokens(text: str, chars_per_token: float) -> int:
    """Estimate the token count of a text from its length.

    Rough on purpose: the figure that counts is the one the triage model bills, so
    `chars_per_token` is meant to be calibrated against the first real invoice.

    Args:
        text: Text to measure.
        chars_per_token: Characters per token, from the configuration.

    Returns:
        The estimated number of tokens.
    """
    return int(len(text) / chars_per_token)


def truncate_to_tokens(text: str, max_tokens: int, chars_per_token: float) -> tuple[str, bool]:
    """Cut a text down to an estimated token budget.

    The cut is moved back to the nearest sentence end when that keeps at least four
    fifths of the budget, so a truncated article rarely ends mid-sentence.

    Args:
        text: Text to cut.
        max_tokens: Token budget.
        chars_per_token: Characters per token, from the configuration.

    Returns:
        The text, and whether it was actually cut.
    """
    budget = int(max_tokens * chars_per_token)
    if len(text) <= budget:
        return text, False

    window = text[:budget]
    floor = int(budget * _MIN_KEPT_RATIO)
    for marker in _SENTENCE_MARKERS:
        cut = window.rfind(marker)
        if cut >= floor:
            return window[: cut + 1].strip(), True
    return window.strip(), True


def html_to_text(raw: str) -> str:
    """Turn a Hacker News comment body into plain text.

    Comment bodies are small HTML fragments: paragraphs, links and HTML entities.

    Args:
        raw: The `text` field of a Hacker News comment.

    Returns:
        The text without markup, with entities decoded.
    """
    paragraphs = re.sub(r"</?p>", "\n\n", raw, flags=re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", "", paragraphs)
    return re.sub(r"\n{3,}", "\n\n", html_module.unescape(stripped)).strip()


def comment_from_payload(payload: dict[str, Any], cfg: EnrichConfig) -> Comment | None:
    """Convert one Hacker News comment payload into a `Comment`.

    Args:
        payload: One Firebase item payload.
        cfg: Enrichment settings.

    Returns:
        The comment, or `None` when it is deleted, dead or carries no text.
    """
    raw = payload.get("text")
    if payload.get("deleted") or payload.get("dead") or not raw:
        return None

    text = html_to_text(str(raw))
    if not text:
        return None

    truncated, _ = truncate_to_tokens(text, cfg.max_comment_tokens, cfg.chars_per_token)
    created_at = payload.get("time")
    return Comment(
        id=f"hn:{payload.get('id')}",
        author=payload.get("by"),
        text=truncated,
        published_at=datetime.fromtimestamp(int(created_at or 0), tz=UTC),
    )


def build_client(cfg: EnrichConfig, report: EnrichReport) -> httpx.Client:
    """Build the HTTP client, counting every request into `report`.

    Args:
        cfg: Enrichment settings.
        report: Report mutated by the request hook.

    Returns:
        A client whose `requests` counter is kept up to date.
    """

    def count_request(request: httpx.Request) -> None:
        report.requests += 1

    return httpx.Client(
        timeout=cfg.timeout_s,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [count_request]},
    )


def fetch_article_text(client: httpx.Client, url: str) -> str:
    """Fetch an article and extract its main text.

    Args:
        client: HTTP client to use.
        url: Article URL.

    Returns:
        The extracted main text.

    Raises:
        httpx.HTTPError: If the page cannot be fetched.
        ValueError: If no main text could be extracted.
    """
    response = client.get(url)
    response.raise_for_status()
    text = trafilatura.extract(
        response.text,
        url=str(response.url),
        # Plain text: markup, menus, cookie banners and ads would only eat into the
        # token budget without carrying meaning. What survives is prose, and tables
        # are kept as text because benchmark numbers are part of the substance.
        output_format="txt",
        include_formatting=False,
        include_links=False,
        include_tables=True,
        # Reader comments on the article page are not collected: the discussion worth
        # reading is the Hacker News thread, fetched separately.
        include_comments=False,
    )
    if not text or not text.strip():
        raise ValueError("no main text found")
    return text


def fetch_comment_ids(client: httpx.Client, story_id: str) -> list[int]:
    """Return the direct replies of a Hacker News story, in API order.

    Args:
        client: HTTP client to use.
        story_id: Hacker News item id, without the `hn:` prefix.

    Returns:
        The ids of the story's direct replies.
    """
    response = client.get(HN_ITEM_API.format(item_id=story_id))
    response.raise_for_status()
    payload = response.json() or {}
    return [int(kid) for kid in payload.get("kids") or []]


def collect_comments(client: httpx.Client, item: Item, cfg: EnrichConfig) -> list[Comment]:
    """Fetch the first top-level comments of a Hacker News story.

    Deleted, dead and empty comments are skipped, and the walk goes on until
    `max_comments` usable comments are collected or the replies are exhausted.

    Args:
        client: HTTP client to use.
        item: The item whose thread to read.
        cfg: Enrichment settings.

    Returns:
        The comments kept, in the order the API returned them.
    """
    comments: list[Comment] = []
    for comment_id in fetch_comment_ids(client, item.id.removeprefix("hn:")):
        if len(comments) >= cfg.max_comments:
            break
        response = client.get(HN_ITEM_API.format(item_id=comment_id))
        response.raise_for_status()
        comment = comment_from_payload(response.json() or {}, cfg)
        if comment is not None:
            comments.append(comment)
    return comments


def enrich_item(client: httpx.Client, item: Item, cfg: EnrichConfig) -> EnrichedItem:
    """Fetch, extract and truncate one item, without ever raising.

    A failure of the article or of its thread becomes a state on the returned object:
    the stage tolerates per-item failure and carries on.

    Args:
        client: HTTP client to use.
        item: The item to enrich.
        cfg: Enrichment settings.

    Returns:
        The enriched item, with status `unavailable` when the article could not be read
        or yielded less than `min_text_tokens` of text.
    """
    fetched_at = datetime.now(UTC)
    text: str | None = None
    error: str | None = None
    try:
        text = fetch_article_text(client, item.url)
    except Exception as exc:  # a failed item must never bring the stage down
        error = f"{type(exc).__name__}: {exc}"

    if text is None:
        return EnrichedItem(
            item_id=item.id,
            url=item.url,
            fingerprint=url_fingerprint(item.url),
            status="unavailable",
            fetched_at=fetched_at,
            title=item.title,
            error=error,
        )

    extracted_tokens = estimate_tokens(text, cfg.chars_per_token)
    if extracted_tokens < cfg.min_text_tokens:
        # A JavaScript shell or an empty body yields a handful of words. Triage must
        # not mistake that for an article, so the item becomes metadata-only. The few
        # tokens found are still kept, for inspection and to avoid fetching again.
        return EnrichedItem(
            item_id=item.id,
            url=item.url,
            fingerprint=url_fingerprint(item.url),
            status="unavailable",
            fetched_at=fetched_at,
            title=item.title,
            text=text,
            text_tokens=extracted_tokens,
            error=f"text too short: {extracted_tokens} tokens (floor {cfg.min_text_tokens})",
        )

    truncated, was_truncated = truncate_to_tokens(text, cfg.max_text_tokens, cfg.chars_per_token)
    comments: list[Comment] = []
    comments_error: str | None = None
    if cfg.max_comments > 0 and item.source == "hn":
        try:
            comments = collect_comments(client, item, cfg)
        except Exception as exc:  # the thread is a bonus, the text is the deliverable
            comments_error = f"{type(exc).__name__}: {exc}"

    return EnrichedItem(
        item_id=item.id,
        url=item.url,
        fingerprint=url_fingerprint(item.url),
        status="ok",
        fetched_at=fetched_at,
        title=item.title,
        text=truncated,
        text_tokens=estimate_tokens(truncated, cfg.chars_per_token),
        text_truncated=was_truncated,
        comments=comments,
        comments_error=comments_error,
    )


def enrich_day(
    day: date,
    items: Iterable[Item],
    cfg: EnrichConfig,
    *,
    limit: int | None = None,
    force: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> EnrichReport:
    """Enrich the best candidates of one day, reusing the cache.

    The cap is applied before the cache is consulted, never after: otherwise a second
    run would move on and enrich the items just below the cap.

    Args:
        day: Day the enriched files belong to.
        items: Candidates, already sorted by descending score.
        cfg: Enrichment settings.
        limit: Overrides `cfg.max_items`, for development runs.
        force: Fetch again even when a cache file exists.
        on_progress: Called with `(done, total)` after each selected item.

    Returns:
        The run report, aggregating both freshly fetched and cached items.
    """
    candidates = list(items)
    cap = limit if limit is not None else cfg.max_items
    selected = candidates[:cap]
    report = EnrichReport(candidates=len(candidates), selected=len(selected))
    started = time.monotonic()

    with build_client(cfg, report) as client:
        for done, item in enumerate(selected, start=1):
            path = enriched_path(day, item.url)
            if path.exists() and not force:
                report.cached += 1
                enriched = EnrichedItem.model_validate(read_json(path))
            else:
                report.fetched += 1
                enriched = enrich_item(client, item, cfg)
                write_json(path, enriched.model_dump(mode="json"))

            if enriched.status == "ok":
                report.with_text += 1
            else:
                report.unavailable += 1
            report.comments_kept += len(enriched.comments)
            report.text_tokens += enriched.text_tokens
            if enriched.comments_error:
                report.thread_failures += 1
            if on_progress is not None:
                on_progress(done, len(selected))

    report.duration_s = round(time.monotonic() - started, 2)
    return report
