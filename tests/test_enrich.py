"""Tests for the enrichment stage: truncation, parsing, cache and failure handling.

Article fetching and comment collection are monkeypatched, so no test hits the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from veille.config import EnrichConfig
from veille.enrich import (
    comment_from_payload,
    enrich_day,
    enrich_item,
    estimate_tokens,
    html_to_text,
    truncate_to_tokens,
    url_fingerprint,
)
from veille.models import EnrichedItem, Item

DAY = date(2026, 9, 16)


def make_item(item_id: str, *, url: str | None = None, points: int = 10) -> Item:
    target = url or f"https://example.com/{item_id}"
    return Item(
        id=f"hn:{item_id}",
        source="hn",
        title=f"title {item_id}",
        url=target,
        discussion_url=f"https://news.ycombinator.com/item?id={item_id}",
        author="author",
        points=points,
        num_comments=3,
        published_at=datetime(2026, 9, 16, 10, 0, tzinfo=UTC),
        kind="story",
    )


class BoomClient:
    """Stand-in client whose every request fails."""

    def get(self, url: str, **kwargs: Any) -> Any:
        raise RuntimeError("network down")


def test_estimate_tokens_divides_by_the_configured_ratio() -> None:
    assert estimate_tokens("x" * 4000, 4.0) == 1000
    assert estimate_tokens("", 4.0) == 0
    assert estimate_tokens("x" * 4000, 2.0) == 2000


def test_short_text_is_not_truncated() -> None:
    text = "A short article."
    assert truncate_to_tokens(text, 1200, 4.0) == (text, False)


def test_truncation_cuts_at_the_budget_without_a_sentence() -> None:
    text = "x" * 2000
    truncated, was_truncated = truncate_to_tokens(text, 100, 4.0)
    assert was_truncated
    assert truncated == "x" * 400


def test_truncation_prefers_a_sentence_boundary() -> None:
    text = "First sentence. " * 100
    truncated, was_truncated = truncate_to_tokens(text, 100, 4.0)
    assert was_truncated
    assert truncated.endswith(".")
    assert 320 <= len(truncated) <= 400


def test_truncation_ignores_a_sentence_too_far_back() -> None:
    text = "Short. " + "y" * 2000
    truncated, was_truncated = truncate_to_tokens(text, 100, 4.0)
    assert was_truncated
    assert len(truncated) == 400
    assert truncated.startswith("Short. ")


def test_comment_html_is_flattened() -> None:
    raw = '<p>Hello &amp; welcome.</p><p>See <a href="https://x.test">this</a>.</p>'
    assert html_to_text(raw) == "Hello & welcome.\n\nSee this."


def test_comment_html_decodes_numeric_entities() -> None:
    assert html_to_text("<p>it&#x27;s fine</p>") == "it's fine"


def test_alive_comment_is_parsed() -> None:
    payload = {
        "id": 49734146,
        "by": "jacobgorm",
        "parent": 49724881,
        "text": "<p>I strongly dislike CUDA.</p>",
        "time": 1789599261,
        "type": "comment",
    }
    comment = comment_from_payload(payload, EnrichConfig())
    assert comment is not None
    assert comment.id == "hn:49734146"
    assert comment.author == "jacobgorm"
    assert comment.text == "I strongly dislike CUDA."
    assert comment.published_at == datetime.fromtimestamp(1789599261, tz=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "deleted": True, "type": "comment"},
        {"id": 2, "dead": True, "type": "comment", "text": "<p>gone</p>"},
        {"id": 3, "type": "comment"},
        {"id": 4, "text": "   ", "type": "comment"},
    ],
)
def test_unusable_comment_is_skipped(payload: dict[str, Any]) -> None:
    assert comment_from_payload(payload, EnrichConfig()) is None


def test_long_comment_is_truncated() -> None:
    payload = {"id": 5, "by": "someone", "text": "<p>" + "word " * 500 + "</p>", "time": 0}
    comment = comment_from_payload(payload, EnrichConfig(max_comment_tokens=100))
    assert comment is not None
    assert comment.text.endswith("word")
    assert len(comment.text) <= 400


def test_enrich_item_reports_an_unreadable_article(monkeypatch: pytest.MonkeyPatch) -> None:
    item = make_item("1")
    enriched = enrich_item(BoomClient(), item, EnrichConfig())  # type: ignore[arg-type]

    assert enriched.status == "unavailable"
    assert enriched.error is not None
    assert "RuntimeError" in enriched.error
    assert enriched.text == ""
    assert enriched.item_id == "hn:1"
    assert enriched.fingerprint


def test_enrich_item_truncates_and_counts_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    item = make_item("1")
    monkeypatch.setattr("veille.enrich.fetch_article_text", lambda client, url: "z" * 10000)
    monkeypatch.setattr("veille.enrich.collect_comments", lambda client, item, cfg: [])

    enriched = enrich_item(BoomClient(), item, EnrichConfig(max_text_tokens=1200))  # type: ignore[arg-type]

    assert enriched.status == "ok"
    assert enriched.text_truncated
    assert enriched.text_tokens == 1200
    assert len(enriched.text) == 4800
    assert enriched.comments == []
    assert enriched.error is None


def test_enrich_item_keeps_the_text_when_the_thread_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = make_item("1")
    # 1,000 characters is about 250 estimated tokens, above the default floor.
    monkeypatch.setattr("veille.enrich.fetch_article_text", lambda client, url: "z" * 1000)

    def broken_thread(client: Any, item: Item, cfg: EnrichConfig) -> list[Any]:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr("veille.enrich.collect_comments", broken_thread)

    enriched = enrich_item(BoomClient(), item, EnrichConfig())  # type: ignore[arg-type]

    assert enriched.status == "ok"
    assert enriched.text == "z" * 1000
    assert enriched.comments_error is not None


def test_extraction_below_the_floor_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    item = make_item("1")
    # 100 characters is about 25 estimated tokens, well under the 200-token floor.
    monkeypatch.setattr("veille.enrich.fetch_article_text", lambda client, url: "y" * 100)

    def must_not_be_called(client: Any, item: Item, cfg: EnrichConfig) -> list[Any]:
        raise AssertionError("a metadata-only item must not spend requests on the thread")

    monkeypatch.setattr("veille.enrich.collect_comments", must_not_be_called)

    enriched = enrich_item(BoomClient(), item, EnrichConfig(min_text_tokens=200))  # type: ignore[arg-type]

    assert enriched.status == "unavailable"
    assert enriched.error == "text too short: 25 tokens (floor 200)"
    # The few tokens found are kept, so the item never has to be fetched twice.
    assert enriched.text == "y" * 100
    assert enriched.text_tokens == 25
    assert enriched.comments == []


def test_extraction_above_the_floor_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    item = make_item("1")
    monkeypatch.setattr("veille.enrich.fetch_article_text", lambda client, url: "y" * 1000)
    monkeypatch.setattr("veille.enrich.collect_comments", lambda client, item, cfg: [])

    enriched = enrich_item(BoomClient(), item, EnrichConfig(min_text_tokens=200))  # type: ignore[arg-type]

    assert enriched.status == "ok"
    assert enriched.text_tokens == 250
    assert enriched.text_truncated is False


def test_a_zero_floor_accepts_any_text(monkeypatch: pytest.MonkeyPatch) -> None:
    item = make_item("1")
    monkeypatch.setattr("veille.enrich.fetch_article_text", lambda client, url: "y" * 40)
    monkeypatch.setattr("veille.enrich.collect_comments", lambda client, item, cfg: [])

    enriched = enrich_item(BoomClient(), item, EnrichConfig(min_text_tokens=0))  # type: ignore[arg-type]

    assert enriched.status == "ok"
    assert enriched.text == "y" * 40


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the enriched cache into a temporary directory."""
    cache = tmp_path / "enriched"
    monkeypatch.setattr(
        "veille.enrich.enriched_path", lambda day, url: cache / f"{abs(hash(url))}.json"
    )
    return cache


@pytest.fixture
def fake_enrich_item(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace the per-item enrichment by a counter, so the loop can be observed."""
    calls = {"count": 0}

    def fake(client: Any, item: Item, cfg: EnrichConfig) -> EnrichedItem:
        calls["count"] += 1
        return EnrichedItem(
            item_id=item.id,
            url=item.url,
            fingerprint=url_fingerprint(item.url),
            status="ok",
            fetched_at=datetime.now(UTC),
            title=item.title,
            text="text " * 100,
            text_tokens=100,
        )

    monkeypatch.setattr("veille.enrich.enrich_item", fake)
    return calls


def test_first_run_fetches_every_selected_item(
    isolated_cache: Path, fake_enrich_item: dict[str, int]
) -> None:
    items = [make_item(str(n), points=100 - n) for n in range(3)]
    report = enrich_day(DAY, items, EnrichConfig(max_items=2))

    assert (report.candidates, report.selected) == (3, 2)
    assert (report.fetched, report.cached) == (2, 0)
    assert report.with_text == 2
    assert report.text_tokens == 200
    assert fake_enrich_item["count"] == 2


def test_second_run_uses_the_cache(isolated_cache: Path, fake_enrich_item: dict[str, int]) -> None:
    items = [make_item(str(n), points=100 - n) for n in range(3)]
    enrich_day(DAY, items, EnrichConfig(max_items=2))
    report = enrich_day(DAY, items, EnrichConfig(max_items=2))

    assert (report.fetched, report.cached) == (0, 2)
    assert fake_enrich_item["count"] == 2
    assert report.with_text == 2


def test_the_cap_applies_before_the_cache(
    isolated_cache: Path, fake_enrich_item: dict[str, int]
) -> None:
    items = [make_item(str(n), points=100 - n) for n in range(3)]
    enrich_day(DAY, items, EnrichConfig(max_items=1))
    report = enrich_day(DAY, items, EnrichConfig(max_items=1))

    assert (report.fetched, report.cached) == (0, 1)
    assert fake_enrich_item["count"] == 1
    # The item just below the cap must not be enriched instead of the cached one.
    assert len(list(isolated_cache.glob("*.json"))) == 1


def test_force_fetches_again(isolated_cache: Path, fake_enrich_item: dict[str, int]) -> None:
    items = [make_item("1")]
    enrich_day(DAY, items, EnrichConfig(max_items=1))
    report = enrich_day(DAY, items, EnrichConfig(max_items=1), force=True)

    assert (report.fetched, report.cached) == (1, 0)
    assert fake_enrich_item["count"] == 2


def test_unavailable_items_are_written_and_counted(
    isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(client: Any, item: Item, cfg: EnrichConfig) -> EnrichedItem:
        return EnrichedItem(
            item_id=item.id,
            url=item.url,
            fingerprint=url_fingerprint(item.url),
            status="unavailable",
            fetched_at=datetime.now(UTC),
            title=item.title,
            error="ValueError: no main text found",
        )

    monkeypatch.setattr("veille.enrich.enrich_item", unavailable)

    report = enrich_day(DAY, [make_item("1")], EnrichConfig(max_items=5))

    assert (report.selected, report.with_text, report.unavailable) == (1, 0, 1)
    assert len(list(isolated_cache.glob("*.json"))) == 1


def test_limit_overrides_the_configured_cap(
    isolated_cache: Path, fake_enrich_item: dict[str, int]
) -> None:
    items = [make_item(str(n), points=100 - n) for n in range(5)]
    report = enrich_day(DAY, items, EnrichConfig(max_items=200), limit=2)

    assert (report.selected, report.fetched) == (2, 2)
