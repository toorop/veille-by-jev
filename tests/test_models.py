"""Tests for the shared models and the deduplication rule."""

from __future__ import annotations

from datetime import UTC, datetime

from veille.models import Item, deduplicate

PUBLISHED = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def make_item(item_id: str, *, url: str, points: int, comments: int = 0) -> Item:
    return Item(
        id=f"hn:{item_id}",
        source="hn",
        title=f"title {item_id}",
        url=url,
        discussion_url=f"https://news.ycombinator.com/item?id={item_id}",
        author="author",
        points=points,
        num_comments=comments,
        published_at=PUBLISHED,
    )


def test_outranks_compares_points_first() -> None:
    stronger = make_item("1", url="https://a", points=10, comments=1)
    weaker = make_item("2", url="https://b", points=5, comments=999)
    assert stronger.outranks(weaker)
    assert not weaker.outranks(stronger)


def test_outranks_breaks_ties_on_comments() -> None:
    chatty = make_item("1", url="https://a", points=10, comments=42)
    quiet = make_item("2", url="https://b", points=10, comments=3)
    assert chatty.outranks(quiet)
    assert not quiet.outranks(chatty)


def test_equal_items_do_not_outrank_each_other() -> None:
    first = make_item("1", url="https://a", points=10, comments=4)
    second = make_item("2", url="https://a", points=10, comments=4)
    assert not first.outranks(second)
    assert not second.outranks(first)


def test_deduplicate_keeps_the_best_item_per_url() -> None:
    items = [
        make_item("1", url="https://same", points=10),
        make_item("2", url="https://same", points=99),
        make_item("3", url="https://other", points=50),
    ]
    assert [item.id for item in deduplicate(items)] == ["hn:2", "hn:3"]


def test_deduplicate_sorts_by_descending_score() -> None:
    items = [make_item(str(n), url=f"https://{n}", points=n) for n in (1, 9, 5)]
    assert [item.points for item in deduplicate(items)] == [9, 5, 1]


def test_deduplicate_accepts_a_generator() -> None:
    items = (make_item(str(n), url=f"https://{n}", points=n) for n in (2, 7))
    assert [item.points for item in deduplicate(items)] == [7, 2]


def test_deduplicate_of_nothing_is_empty() -> None:
    assert deduplicate([]) == []
