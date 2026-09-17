"""Tests for the collection window and the configuration loading."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from veille.config import MAX_HITS_PER_QUERY, CollectConfig, day_window, load_sources_config


def test_civil_day_window_is_a_plain_day() -> None:
    window = day_window(date(2026, 9, 16), "Europe/Paris")
    assert window.day == date(2026, 9, 16)
    assert window.timezone == "Europe/Paris"
    assert window.hours == 24
    assert window.start == datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 9, 16, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "expected_hours", "expected_start", "expected_end"),
    [
        (
            date(2026, 3, 29),
            23,
            datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
        ),
        (
            date(2026, 10, 25),
            25,
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
        ),
    ],
)
def test_civil_day_window_follows_daylight_saving(
    day: date, expected_hours: int, expected_start: datetime, expected_end: datetime
) -> None:
    """A Paris civil day lasts 23 h when summer time starts and 25 h when it ends."""
    window = day_window(day, "Europe/Paris")
    assert window.hours == expected_hours
    assert window.start == expected_start
    assert window.end == expected_end


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        day_window(date(2026, 9, 16), "Mars/Olympus")


def test_shipped_configuration_is_valid() -> None:
    settings = load_sources_config()
    assert settings.collect.timezone == "Europe/Paris"
    assert settings.collect.min_points == 1
    assert settings.sources["hn"].enabled
    assert settings.sources["hn"].hits_per_page <= MAX_HITS_PER_QUERY


def test_custom_configuration_is_parsed_and_disabled_sources_are_skipped(
    tmp_path: Path,
) -> None:
    custom = tmp_path / "sources.toml"
    custom.write_text(
        "[collect]\n"
        'timezone = "UTC"\n'
        "min_points = 5\n"
        "\n"
        "[sources.hn]\n"
        'endpoint = "https://example.invalid/search"\n'
        "hits_per_page = 100\n"
        "max_pages = 3\n"
        "\n"
        "[sources.off]\n"
        'endpoint = "https://example.invalid/off"\n'
        "enabled = false\n",
        encoding="utf-8",
    )
    settings = load_sources_config(custom)
    assert settings.collect.timezone == "UTC"
    assert settings.collect.min_points == 5
    assert settings.sources["hn"].max_pages == 3
    assert set(settings.enabled_sources()) == {"hn"}


def test_missing_configuration_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sources_config(tmp_path / "absent.toml")


def test_collection_carries_no_item_cap() -> None:
    """The cap belongs to the stages that pay, so it must not live in collect."""
    assert "target_items" not in CollectConfig.model_fields
