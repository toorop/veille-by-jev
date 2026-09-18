"""Tests for the collection window and the configuration loading."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from veille.config import (
    MAX_HITS_PER_QUERY,
    CollectConfig,
    day_window,
    load_questions_config,
    load_sources_config,
    rolling_window,
)


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


def test_rolling_window_ends_at_the_run_and_lasts_the_asked_length() -> None:
    """The whole point of the rolling mode: the batch ends now, not at midnight."""
    now = datetime(2026, 9, 17, 6, 17, tzinfo=UTC)
    window = rolling_window(now, "Europe/Paris", 24)
    assert window.end == now
    assert window.start == datetime(2026, 9, 16, 6, 17, tzinfo=UTC)
    assert window.hours == 24
    assert window.timezone == "Europe/Paris"


def test_rolling_window_is_labelled_by_the_day_it_ends_in() -> None:
    """23:30 UTC is already the next day in Paris: the label follows the zone, not UTC."""
    window = rolling_window(datetime(2026, 9, 16, 23, 30, tzinfo=UTC), "Europe/Paris", 24)
    assert window.day == date(2026, 9, 17)


def test_rolling_window_accepts_another_length() -> None:
    now = datetime(2026, 9, 17, 6, 17, tzinfo=UTC)
    window = rolling_window(now, "Europe/Paris", 6)
    assert window.hours == 6
    assert window.start == datetime(2026, 9, 17, 0, 17, tzinfo=UTC)


def test_rolling_window_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        rolling_window(datetime(2026, 9, 17, 6, 17), "Europe/Paris", 24)


def test_rolling_window_rejects_an_unknown_timezone() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        rolling_window(datetime(2026, 9, 17, 6, 17, tzinfo=UTC), "Mars/Olympus", 24)


def test_window_hours_is_bounded_in_the_config() -> None:
    """A zero or absurd length would collect nothing, or everything ever posted."""
    assert CollectConfig(window_hours=12).window_hours == 12
    with pytest.raises(ValidationError):
        CollectConfig(window_hours=0)
    with pytest.raises(ValidationError):
        CollectConfig(window_hours=169)


def test_shipped_configuration_is_valid() -> None:
    settings = load_sources_config()
    assert settings.collect.timezone == "Europe/Paris"
    assert settings.collect.min_points == 1
    assert settings.sources["hn"].enabled
    assert settings.sources["hn"].hits_per_page <= MAX_HITS_PER_QUERY
    assert settings.enrich.max_items == 200
    assert settings.enrich.max_text_tokens == 2000
    assert settings.enrich.min_text_tokens == 200
    assert settings.enrich.max_comments == 5


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


def test_shipped_grid_is_valid() -> None:
    grid = load_questions_config()
    assert grid.triage.model == "jev-latest"
    assert grid.triage.score_penalty_z == 1.0
    assert grid.triage.min_adjusted_score == 2.0
    assert grid.triage.price_per_mtok_usd == 0.042
    assert grid.triage.max_items == 200
    assert list(grid.questions) == ["interest", "density", "category", "primary_source"]

    interest = grid.questions["interest"]
    assert interest.type == "score"
    assert interest.instructions
    assert isinstance(interest.criteria, list)
    assert len(interest.criteria) == 5

    assert grid.questions["density"].type == "score"
    assert grid.questions["category"].type == "choice"
    assert grid.questions["primary_source"].type == "noul"

    # The category is a label for the digest, not a quality signal, so it carries no
    # weight; the three others do, and they are normalised before use.
    assert grid.aggregation.weights == {"interest": 0.5, "density": 0.3, "primary_source": 0.2}
    assert "category" not in grid.aggregation.weights
    assert grid.aggregation.scale == 4.0
    assert sum(grid.aggregation.normalised().values()) == pytest.approx(1.0)


def test_a_weight_naming_an_unknown_question_is_rejected(tmp_path: Path) -> None:
    """A typo there would silently drop a question from the ranking."""
    custom = tmp_path / "questions.toml"
    custom.write_text(
        '[questions.interest]\ntype = "score"\ncriteria = ["a", "b"]\n'
        "\n[aggregation.weights]\ninterst = 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="unknown questions"):
        load_questions_config(custom)


def test_a_choice_question_is_parsed(tmp_path: Path) -> None:
    custom = tmp_path / "questions.toml"
    custom.write_text(
        "[questions.category]\n"
        'type = "choice"\n'
        'instructions = "Catégorie"\n'
        "[questions.category.criteria]\n"
        'research = "papier"\n'
        'tooling = "outil"\n',
        encoding="utf-8",
    )
    grid = load_questions_config(custom)
    assert grid.questions["category"].criteria == {"research": "papier", "tooling": "outil"}


def test_a_noul_question_needs_no_criteria(tmp_path: Path) -> None:
    custom = tmp_path / "questions.toml"
    custom.write_text(
        '[questions.primary]\ntype = "noul"\ninstructions = "Source primaire ?"\n',
        encoding="utf-8",
    )
    assert load_questions_config(custom).questions["primary"].criteria is None


def test_a_score_without_levels_is_rejected(tmp_path: Path) -> None:
    custom = tmp_path / "questions.toml"
    custom.write_text('[questions.interest]\ntype = "score"\n', encoding="utf-8")
    with pytest.raises(ValidationError, match="ordered list of levels"):
        load_questions_config(custom)


def test_a_choice_with_a_list_is_rejected(tmp_path: Path) -> None:
    custom = tmp_path / "questions.toml"
    custom.write_text(
        '[questions.category]\ntype = "choice"\ncriteria = ["a", "b"]\n', encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="table of options"):
        load_questions_config(custom)


def test_missing_grid_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_questions_config(tmp_path / "absent.toml")


def test_collection_carries_no_item_cap() -> None:
    """The cap belongs to the stages that pay, so it must not live in collect."""
    assert "target_items" not in CollectConfig.model_fields
