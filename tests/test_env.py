"""Tests for the local `.env` loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from veille.env import load_dotenv, parse_dotenv


def test_plain_assignments_are_parsed() -> None:
    parsed = parse_dotenv("TYPESAFE_API_KEY=sk-abc\nWRITE_LLM_MODEL=mistral\n")
    assert parsed == {"TYPESAFE_API_KEY": "sk-abc", "WRITE_LLM_MODEL": "mistral"}


def test_comments_and_blank_lines_are_ignored() -> None:
    parsed = parse_dotenv("# a comment\n\n   \nKEY=value\n  # indented comment\n")
    assert parsed == {"KEY": "value"}


def test_quotes_are_removed() -> None:
    parsed = parse_dotenv('A="double"\nB=\'single\'\nC="unbalanced\n')
    assert parsed == {"A": "double", "B": "single", "C": '"unbalanced'}


def test_export_prefix_is_tolerated() -> None:
    assert parse_dotenv("export KEY=value\n") == {"KEY": "value"}


def test_empty_value_is_kept_as_empty_string() -> None:
    assert parse_dotenv("KEY=\n") == {"KEY": ""}


def test_equals_signs_inside_the_value_are_kept() -> None:
    assert parse_dotenv("KEY=a=b=c\n") == {"KEY": "a=b=c"}


def test_surrounding_spaces_are_trimmed() -> None:
    assert parse_dotenv("  KEY  =  value  \n") == {"KEY": "value"}


@pytest.mark.parametrize("line", ["NOEQUALS", "=novalue", "2BAD=x", "BAD-KEY=x", "-=x"])
def test_malformed_lines_are_ignored(line: str) -> None:
    assert parse_dotenv(line + "\n") == {}


def test_load_sets_missing_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VEILLE_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('VEILLE_TEST_KEY="from-file"\n', encoding="utf-8")

    loaded = load_dotenv(env_file)

    assert loaded == {"VEILLE_TEST_KEY": "from-file"}
    assert os.environ["VEILLE_TEST_KEY"] == "from-file"


def test_load_never_overrides_the_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VEILLE_TEST_KEY", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("VEILLE_TEST_KEY=from-file\n", encoding="utf-8")

    loaded = load_dotenv(env_file)

    assert loaded == {}
    assert os.environ["VEILLE_TEST_KEY"] == "from-shell"


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "absent.env") == {}


def test_shipped_example_has_no_values() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")
    parsed = parse_dotenv(example)
    assert parsed, "the example should list variable names"
    assert all(value == "" for value in parsed.values()), "the example must carry no values"
    assert "TYPESAFE_API_KEY" in parsed
    assert "WRITE_LLM_API_KEY" in parsed
    # Nothing else is required: the triage provider has working defaults for its base
    # URL and model name, so they must not be advertised as mandatory settings.
    assert "TYPESAFE_BASE_URL" not in parsed
    assert "TYPESAFE_DEFAULT_MODEL" not in parsed
