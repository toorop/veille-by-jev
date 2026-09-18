"""Tests for the command-line surface.

No test here touches the network: the idempotence tests stub the output path, and the
full-run tests stub the stages themselves.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from veille import __version__
from veille.cli import StageResult, app

runner = CliRunner()

STAGES = ("collect", "enrich", "triage", "write")


def stub_stages(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    conflict_on: str | None = None,
    fail_on: str | None = None,
) -> None:
    """Replace the four stage functions by recorders, so a run is testable in isolation."""
    for stage in STAGES:
        if stage == fail_on:

            def boom(*_args: object, _stage: str = stage, **_kwargs: object) -> StageResult:
                calls.append(_stage)
                raise typer.Exit(code=2)

            monkeypatch.setattr(f"veille.cli._{stage}", boom)
            continue

        def record(
            *_args: object, _stage: str = stage, **_kwargs: object
        ) -> StageResult:
            calls.append(_stage)
            return StageResult(_stage, skipped=True, conflict=_stage == conflict_on)

        monkeypatch.setattr(f"veille.cli._{stage}", record)


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_the_collect_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "collect" in result.stdout


def test_collect_documents_its_options() -> None:
    result = runner.invoke(app, ["collect", "--help"])
    assert result.exit_code == 0
    assert "--date" in result.stdout
    assert "--force" in result.stdout


def test_invalid_date_is_rejected_with_a_clear_message() -> None:
    result = runner.invoke(app, ["collect", "--date", "16/09/2026"])
    assert result.exit_code == 2
    assert "Invalid date" in result.output


def test_missing_date_defaults_to_today_in_the_configured_zone() -> None:
    """Omitting --date is now the rolling mode, so it must resolve to a real day."""
    from datetime import UTC, datetime

    from veille.cli import _resolve_day
    from veille.config import civil_day

    today = civil_day(datetime.now(UTC), "Europe/Paris")
    assert _resolve_day(None) == today
    assert _resolve_day("2026-09-16") == date(2026, 9, 16)


def test_an_existing_output_file_skips_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from veille.config import day_window
    from veille.store import iso_utc

    wanted = day_window(date(2026, 9, 16), "Europe/Paris")
    target = tmp_path / "items.json"
    target.write_text(
        json.dumps(
            {
                "stats": {"candidates": 7},
                "generated_at": "2026-09-17T00:00:00Z",
                "window": {
                    "start": iso_utc(wanted.start),
                    "end": iso_utc(wanted.end),
                    "hours": wanted.hours,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("veille.cli.items_path", lambda day: target)

    result = runner.invoke(app, ["collect", "--date", "2026-09-16"])

    assert result.exit_code == 0
    assert "already exists — nothing to do" in result.stdout
    assert "7" in result.stdout
    assert "USD 0.00" in result.stdout
    assert "the requested one" in result.stdout


def test_a_different_stored_window_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file holding another window must never pass for the one that was asked for."""
    from veille.config import day_window
    from veille.store import iso_utc

    stale = day_window(date(2026, 9, 10), "Europe/Paris")
    target = tmp_path / "items.json"
    target.write_text(
        json.dumps(
            {
                "stats": {"candidates": 7},
                "generated_at": "2026-09-10T00:00:00Z",
                "window": {
                    "start": iso_utc(stale.start),
                    "end": iso_utc(stale.end),
                    "hours": stale.hours,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("veille.cli.items_path", lambda day: target)

    result = runner.invoke(app, ["collect", "--date", "2026-09-16"])

    assert result.exit_code == 0
    assert "differs from the stored one" in result.stdout
    assert "2026-09-09T22:00:00Z" in result.stdout  # the stored start, not the wanted one


def test_display_path_falls_back_to_absolute_outside_the_project(tmp_path: Path) -> None:
    from veille.cli import _display_path

    assert _display_path(tmp_path / "items.json") == str(tmp_path / "items.json")
    assert _display_path(Path("data") / date(2026, 9, 16).isoformat()) == "data/2026-09-16"


def test_the_callback_loads_the_local_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr("veille.cli.load_dotenv", lambda: calls.append(1))

    runner.invoke(app, ["collect", "--date", "2026-09-16"])

    assert calls == [1]


def test_dumping_the_prompt_writes_it_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le dump sert justement à tester un modèle ailleurs : il ne doit rien appeler."""
    scores = tmp_path / "scores.json"
    scores.write_text(
        json.dumps({"schema_version": 1, "settings": {}, "scores": []}), encoding="utf-8"
    )
    monkeypatch.setattr("veille.write.scores_path", lambda day: scores)
    target = tmp_path / "prompt.json"

    result = runner.invoke(app, ["write", "--date", "2026-09-16", "--dump-prompt", str(target)])

    assert result.exit_code == 0
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["date"] == "2026-09-16"
    assert "no model call" in result.stdout


def test_the_help_lists_the_whole_night_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run " in result.stdout


def test_a_full_run_calls_the_four_stages_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    stub_stages(monkeypatch, calls)

    result = runner.invoke(app, ["run", "--date", "2026-09-16"])

    assert result.exit_code == 0
    assert calls == list(STAGES)
    assert "0 ran, 4 skipped" in result.stdout


def test_a_full_run_stops_when_the_stored_window_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch from another window must not be written up as if it were the asked one."""
    calls: list[str] = []
    stub_stages(monkeypatch, calls, conflict_on="collect")

    result = runner.invoke(app, ["run", "--date", "2026-09-16"])

    assert result.exit_code == 1
    assert calls == ["collect"]
    assert "differs" in result.output


def test_a_failing_stage_stops_the_run_and_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    stub_stages(monkeypatch, calls, fail_on="enrich")

    result = runner.invoke(app, ["run", "--date", "2026-09-16"])

    assert result.exit_code == 2
    assert calls == ["collect", "enrich"]
    assert "stopped at enrich" in result.output
    assert "nothing after enrich was attempted" in result.output


def test_the_total_separates_this_run_from_earlier_spending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped stage that cost money before must not look free, nor bill this run."""
    calls: list[str] = []
    stub_stages(monkeypatch, calls)

    def spent(*_args: object, **_kwargs: object) -> StageResult:
        calls.append("triage")
        return StageResult("triage", skipped=True, already_usd=0.0166)

    monkeypatch.setattr("veille.cli._triage", spent)

    result = runner.invoke(app, ["run", "--date", "2026-09-16"])

    assert "USD 0.000000 for this run" in result.stdout
    assert "USD 0.016600 for this window" in result.stdout


def test_the_bare_invocation_runs_the_whole_night(monkeypatch: pytest.MonkeyPatch) -> None:
    """`vbj` with no argument is the cron line: it must run, not print help."""
    seen: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(
        "veille.cli.full_run", lambda raw, *, force: seen.append((raw, force))
    )

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert seen == [(None, False)]


def test_an_explicit_command_does_not_trigger_a_full_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare-invocation hook must not fire when a stage is named."""
    seen: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(
        "veille.cli.full_run", lambda raw, *, force: seen.append((raw, force))
    )

    result = runner.invoke(app, ["collect", "--help"])

    assert result.exit_code == 0
    assert seen == []
