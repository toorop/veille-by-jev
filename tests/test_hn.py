"""Tests for the Hacker News parsing and collection behaviour.

The HTTP layer is monkeypatched, so the suite never touches the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from veille.config import CollectConfig, SourceSettings, day_window
from veille.models import Window
from veille.sources import hn

HIT: dict[str, Any] = {
    "objectID": "49724881",
    "title": "Nvidia announces native GPU programming in Rust",
    "url": "https://developer.nvidia.com/blog/introducing-cuda-rust/",
    "author": "nonmaskable",
    "points": 858,
    "num_comments": 341,
    "created_at_i": 1789557353,
    "_tags": ["story", "author_nonmaskable", "story_49724881"],
}


def make_settings(**overrides: Any) -> SourceSettings:
    return SourceSettings(
        endpoint="https://example.invalid/api/v1/search",
        **overrides,
    )


def make_window() -> Window:
    return day_window(date(2026, 9, 16), "Europe/Paris")


def test_hit_is_mapped_to_an_item() -> None:
    item = hn._item_from_hit(dict(HIT))
    assert item is not None
    assert item.id == "hn:49724881"
    assert item.source == "hn"
    assert item.title == HIT["title"]
    assert item.url == HIT["url"]
    assert item.discussion_url == "https://news.ycombinator.com/item?id=49724881"
    assert item.author == "nonmaskable"
    assert item.points == 858
    assert item.num_comments == 341
    assert item.kind == "story"
    assert item.published_at == datetime(2026, 9, 16, 11, 15, 53, tzinfo=UTC)


def test_item_without_url_falls_back_to_the_discussion_thread() -> None:
    hit = dict(HIT)
    del hit["url"]
    item = hn._item_from_hit(hit)
    assert item is not None
    assert item.url == "https://news.ycombinator.com/item?id=49724881"


def test_blank_title_gets_a_placeholder() -> None:
    item = hn._item_from_hit(dict(HIT, title="   "))
    assert item is not None
    assert item.title == "(sans titre)"


def test_missing_scores_default_to_zero() -> None:
    hit = dict(HIT)
    del hit["points"]
    del hit["num_comments"]
    item = hn._item_from_hit(hit)
    assert item is not None
    assert (item.points, item.num_comments) == (0, 0)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["story", "show_hn", "front_page"], "show_hn"),
        (["story", "ask_hn"], "ask_hn"),
        (["story", "job"], "job"),
        (["story"], "story"),
        ([], "story"),
    ],
)
def test_the_most_specific_kind_wins(tags: list[str], expected: str) -> None:
    assert hn._kind_from_tags(tags) == expected


@pytest.mark.parametrize("missing", ["objectID", "created_at_i"])
def test_unusable_hit_is_dropped(missing: str) -> None:
    hit = dict(HIT)
    del hit[missing]
    assert hn._item_from_hit(hit) is None


def test_collect_keeps_candidates_above_the_score_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = [
        dict(HIT, objectID="1", points=10, url="https://example.com/a"),
        dict(HIT, objectID="2", points=99, url="https://example.com/a"),
        dict(HIT, objectID="3", points=0, url="https://example.com/b"),
        dict(HIT, objectID="4", points=5, url="https://example.com/c"),
    ]
    monkeypatch.setattr(hn, "_fetch_page", lambda *args, **kwargs: {"hits": hits, "nbPages": 1})

    outcome = hn.collect(make_window(), make_settings(), CollectConfig(min_points=1))

    assert outcome.report.status == "ok"
    assert outcome.report.fetched == 4
    assert outcome.report.kept == 2
    # "https://example.com/a" is posted twice: the best entry survives (hn:2), and
    # the 0-point story is dropped.
    assert [item.id for item in outcome.items] == ["hn:2", "hn:4"]


def test_collect_reports_a_network_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(hn, "_fetch_page", boom)

    outcome = hn.collect(make_window(), make_settings(), CollectConfig())

    assert outcome.report.status == "error"
    assert outcome.items == []
    assert "ConnectError" in (outcome.report.error or "")


def test_collect_stops_on_a_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(client: Any, cfg: SourceSettings, window: Window, page: int) -> dict[str, Any]:
        calls.append(page)
        return {"hits": [dict(HIT, objectID=str(page), url=f"https://example.com/{page}")]}

    monkeypatch.setattr(hn, "_fetch_page", fake)

    outcome = hn.collect(
        make_window(), make_settings(hits_per_page=10, max_pages=5), CollectConfig()
    )

    assert calls == [0]
    assert outcome.report.requests == 1


def test_collect_stops_on_the_last_announced_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(client: Any, cfg: SourceSettings, window: Window, page: int) -> dict[str, Any]:
        calls.append(page)
        return {
            "hits": [dict(HIT, objectID=str(page), url=f"https://example.com/{page}")],
            "nbPages": 2,
        }

    monkeypatch.setattr(hn, "_fetch_page", fake)

    outcome = hn.collect(
        make_window(), make_settings(hits_per_page=1, max_pages=5), CollectConfig()
    )

    assert calls == [0, 1]
    assert outcome.report.requests == 2
