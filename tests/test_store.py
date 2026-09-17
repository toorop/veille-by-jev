"""Tests for the data layout, the fingerprints and the atomic writes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from veille.store import (
    digest_path,
    enriched_dir,
    items_path,
    read_json,
    scores_path,
    url_fingerprint,
    write_json,
)


def test_paths_are_derived_from_the_date() -> None:
    day = date(2026, 9, 16)
    assert items_path(day).as_posix().endswith("data/2026-09-16/items.json")
    assert scores_path(day).as_posix().endswith("data/2026-09-16/scores.json")
    assert enriched_dir(day).as_posix().endswith("data/2026-09-16/enriched")
    assert digest_path(day).as_posix().endswith("digest/2026-09-16.md")


def test_fingerprint_is_stable_url_specific_and_short() -> None:
    reference = url_fingerprint("https://example.com/a")
    assert reference == url_fingerprint("https://example.com/a")
    assert reference != url_fingerprint("https://example.com/b")
    assert len(reference) == 16


def test_fingerprint_ignores_surrounding_whitespace() -> None:
    assert url_fingerprint("  https://example.com/a\n") == url_fingerprint("https://example.com/a")


def test_write_json_round_trips_and_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "items.json"
    write_json(target, {"titre": "éàü", "items": [1, 2]})
    assert read_json(target) == {"titre": "éàü", "items": [1, 2]}


def test_write_json_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "items.json"
    write_json(target, {"items": []})
    assert [path.name for path in tmp_path.iterdir()] == ["items.json"]


def test_write_json_keeps_accents_readable(tmp_path: Path) -> None:
    target = tmp_path / "items.json"
    write_json(target, {"title": "Le café est prêt"})
    assert "café" in target.read_text(encoding="utf-8")


def test_write_json_replaces_the_previous_content(tmp_path: Path) -> None:
    target = tmp_path / "items.json"
    write_json(target, {"items": [1, 2, 3]})
    write_json(target, {"items": [1]})
    assert read_json(target) == {"items": [1]}
