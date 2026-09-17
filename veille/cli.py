"""Command-line interface of the pipeline.

One command per stage, with files as the interface: `collect`, then `enrich`,
`triage` and `write` (later stages). Each command can be replayed on its own for a
given date, and replaying a finished stage costs nothing unless `--force` is passed.

Language convention: the code, the CLI and the configuration files are English. The
only French output is the digest itself, because a French digest is the point of the
project.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from typesafe_sdk import TypeSafeError

from veille import __version__
from veille.config import day_window, load_questions_config, load_sources_config
from veille.enrich import enrich_day
from veille.env import load_dotenv
from veille.models import CollectOutcome, Item, ItemScore, SourceReport, deduplicate
from veille.sources import hn
from veille.store import (
    ROOT,
    enriched_dir,
    iso_utc,
    items_path,
    read_json,
    scores_path,
    write_json,
)
from veille.triage import triage_day

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="veille-by-jev — night watch over Hacker News, turned into a French Markdown digest.",
)

SOURCE_MODULES = {"hn": hn}

FIELD_WIDTH = 10


def _fail(message: str, code: int = 2) -> NoReturn:
    """Print an error on stderr and exit with `code`."""
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(code=code)


def _field(label: str, value: str) -> str:
    """Render one aligned line of the end-of-run report."""
    return f"  {label:<{FIELD_WIDTH}} : {value}"


def _display_path(path: Path) -> str:
    """Render a path relative to the project root, or absolute when it is outside."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _preview_value(score: ItemScore) -> float:
    """Rank items for the end-of-run preview, on the value triage ranked them by."""
    return score.adjusted if score.adjusted is not None else -1.0


def _parse_day(raw: str) -> date:
    """Parse a `YYYY-MM-DD` option value, exiting with a clear message otherwise."""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        _fail(f"Invalid date: {raw!r}. Expected format: YYYY-MM-DD (for example 2026-09-16).")


def _version_callback(value: bool) -> None:
    """Print the version and exit, as an eager `--version` option."""
    if value:
        typer.echo(f"veille-by-jev {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    """Run the veille-by-jev pipeline, one subcommand per stage."""
    # Read .env before any stage runs, so that a locally stored API key is available
    # without a shell setup. A variable already exported wins over the file.
    load_dotenv()


@app.command()
def collect(
    raw_date: Annotated[str, typer.Option("--date", help="Civil day to collect, as YYYY-MM-DD.")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Collect again even if data/<date>/items.json exists."),
    ] = False,
) -> None:
    """Stage 1 — collect the enabled sources into `data/<date>/items.json`.

    No model call at all: this stage is deliberately dumb and verifiable. A source
    failure is recorded in the output file and does not bring the command down.
    Running it again on a date already collected does nothing unless `--force` is
    passed.

    Output is deliberately uncapped: every candidate above the score floor is
    written, because collection costs a single request. The cap that bounds the
    priced stages is applied by the stage that pays for it.
    """
    day = _parse_day(raw_date)
    try:
        settings = load_sources_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/sources.toml:\n{exc}")

    window = day_window(day, settings.collect.timezone)
    out_path = items_path(day)

    # Idempotence invariant: without --force, never redo work already done.
    if out_path.exists() and not force:
        existing = read_json(out_path)
        stats = existing.get("stats", {})
        already_selected = stats.get("candidates", "?")
        collected_at = existing.get("generated_at", "?")
        typer.echo(f"{_display_path(out_path)} already exists — nothing to do.")
        typer.echo(_field("items", f"{already_selected} (collected at {collected_at})"))
        typer.echo(_field("path", str(out_path)))
        typer.echo(_field("re-run", "add --force to collect again"))
        typer.echo(_field("cost", "USD 0.00 (no model call)"))
        return

    enabled = settings.enabled_sources()
    if not enabled:
        _fail("No source is enabled in config/sources.toml.")

    started = time.monotonic()
    outcomes: list[CollectOutcome] = []
    for name, source_cfg in enabled.items():
        module = SOURCE_MODULES.get(source_cfg.type)
        if module is None:
            outcomes.append(
                CollectOutcome(
                    report=SourceReport(
                        source=name,
                        label=source_cfg.label,
                        status="error",
                        error=f"unimplemented source type: {source_cfg.type!r}",
                    )
                )
            )
            continue
        outcomes.append(module.collect(window, source_cfg, settings.collect, name))
    duration_s = round(time.monotonic() - started, 2)

    # Merge and deduplicate across sources. Deliberately uncapped: collection is
    # free, so items.json is a full snapshot of the day's candidates.
    merged = deduplicate(item for outcome in outcomes for item in outcome.items)
    fetched = sum(outcome.report.fetched for outcome in outcomes)
    after_filter = sum(outcome.report.kept for outcome in outcomes)

    # --- Report: the per-source lines come before any decision, so that an empty
    # run reads either as "source down" or as "quiet day" ---
    typer.echo(f"vbj collect --date {day.isoformat()}")
    utc_range = f"{iso_utc(window.start)} → {iso_utc(window.end)}"
    typer.echo(_field("timezone", f"{window.timezone} — UTC window {utc_range}"))
    for outcome in outcomes:
        report = outcome.report
        if report.status == "ok":
            typer.echo(
                _field(
                    report.source,
                    f"ok — {report.fetched} raw items, "
                    f"{report.kept} kept by the points >= {settings.collect.min_points} filter, "
                    f"{report.requests} request(s), {report.duration_s} s",
                )
            )
        else:
            typer.secho(
                _field(report.source, f"failed — {report.error}"),
                err=True,
                fg=typer.colors.YELLOW,
            )

    if not merged:
        typer.echo(_field("candidates", f"{after_filter} after filter → 0 kept"))
        typer.echo(_field("cost", "USD 0.00 — no model call at this stage"))
        _fail(
            f"Nothing to write for {day.isoformat()}: a source failed, or the window has no "
            f"item above min_points={settings.collect.min_points} (see the report above).",
            code=1,
        )

    published = [item.published_at for item in merged]
    payload = {
        "schema_version": 2,
        "date": day.isoformat(),
        "timezone": window.timezone,
        "generated_at": iso_utc(datetime.now(UTC)),
        "window": {
            "start": iso_utc(window.start),
            "end": iso_utc(window.end),
            "hours": window.hours,
        },
        "settings": {"min_points": settings.collect.min_points},
        "sources": [outcome.report.model_dump(mode="json") for outcome in outcomes],
        "stats": {
            "fetched": fetched,
            "after_score_filter": after_filter,
            "candidates": len(merged),
            "published_at_min": iso_utc(min(published)),
            "published_at_max": iso_utc(max(published)),
            "duration_s": duration_s,
            "model_calls": 0,
            "cost_usd": 0.0,
        },
        "items": [item.model_dump(mode="json") for item in merged],
    }
    write_json(out_path, payload)

    typer.echo(
        _field(
            "candidates",
            f"{after_filter} after filter → {len(merged)} after deduplication, all written",
        )
    )
    typer.echo(_field("written", f"{_display_path(out_path)} ({out_path.stat().st_size} bytes)"))
    typer.echo(_field("published", f"{iso_utc(min(published))} → {iso_utc(max(published))}"))
    typer.echo(_field("cost", "USD 0.00 — no model call at this stage"))


@app.command()
def enrich(
    raw_date: Annotated[str, typer.Option("--date", help="Civil day to enrich, as YYYY-MM-DD.")],
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Enrich at most N candidates (development aid)."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Fetch again even when a cache file already exists."),
    ] = False,
) -> None:
    """Stage 2 — fetch and extract the articles into `data/<date>/enriched/`.

    Reads `data/<date>/items.json`, keeps the best `enrich.max_items`
    candidates, fetches each article, extracts its main text, truncates it to
    an estimated token budget and collects the first Hacker News comments.
    Every item is cached under a URL fingerprint, so a second run downloads
    nothing again.

    An article that cannot be read becomes an `unavailable` record instead of
    a failure: the item stays in the batch and the stage carries on.
    """
    day = _parse_day(raw_date)
    try:
        settings = load_sources_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/sources.toml:\n{exc}")

    source_path = items_path(day)
    if not source_path.exists():
        _fail(f"{_display_path(source_path)} not found: run `vbj collect --date {day}` first.")

    items = [Item.model_validate(raw) for raw in read_json(source_path)["items"]]
    cfg = settings.enrich
    cap = limit if limit is not None else cfg.max_items

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            typer.echo(_field("progress", f"{done}/{total} items"))

    report = enrich_day(day, items, cfg, limit=limit, force=force, on_progress=progress)
    out_dir = enriched_dir(day)

    typer.echo(f"vbj enrich --date {day.isoformat()}")
    typer.echo(
        _field(
            "selection",
            f"{report.selected} of {report.candidates} candidates (cap {cap}, "
            f"{report.fetched} fetched, {report.cached} from cache)",
        )
    )
    typer.echo(
        _field(
            "text",
            f"{report.with_text} with text, {report.unavailable} unavailable, "
            f"{report.text_tokens} estimated tokens (about {cfg.chars_per_token} characters each)",
        )
    )
    typer.echo(
        _field(
            "comments",
            f"{report.comments_kept} kept, {report.thread_failures} thread(s) unreadable",
        )
    )
    typer.echo(_field("requests", f"{report.requests} HTTP, {report.duration_s} s, no model call"))
    typer.echo(
        _field("written", f"{_display_path(out_dir)}/ ({len(list(out_dir.glob('*.json')))} files)")
    )
    typer.echo(_field("cost", "USD 0.00 — no model call at this stage"))


@app.command()
def triage(
    raw_date: Annotated[str, typer.Option("--date", help="Civil day to score, as YYYY-MM-DD.")],
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Score at most N candidates (development aid)."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Score again even when scores.json already exists."),
    ] = False,
) -> None:
    """Stage 3 — judge the enriched candidates into `data/<date>/scores.json`.

    Asks the questions of `config/questions.toml` to the ranking engine, one call
    per item, and stores every typed answer with its confidence and distribution.
    An item whose weakest confidence falls below the configured threshold is
    dropped without discussion.

    The grid is written next to the scores, so a ranking can always be traced back
    to the questions that produced it. Replaying a scored day costs nothing unless
    `--force` is passed.
    """
    day = _parse_day(raw_date)
    try:
        questions_cfg = load_questions_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/questions.toml:\n{exc}")

    cfg = questions_cfg.triage
    out_path = scores_path(day)

    # Idempotence matters most here: this is the stage that spends money.
    if out_path.exists() and not force:
        existing = read_json(out_path)
        stats = existing.get("stats", {})
        typer.echo(f"{_display_path(out_path)} already exists — nothing to do.")
        typer.echo(
            _field(
                "scores", f"{stats.get('selected', '?')} items (model {stats.get('model', '?')})"
            )
        )
        typer.echo(_field("cost", f"USD {stats.get('cost_usd', 0.0)} (already spent)"))
        typer.echo(_field("re-run", "add --force to score again"))
        return

    source_path = items_path(day)
    if not source_path.exists():
        _fail(f"{_display_path(source_path)} not found: run `vbj collect --date {day}` first.")

    items = [Item.model_validate(raw) for raw in read_json(source_path)["items"]]
    cap = limit if limit is not None else cfg.max_items

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            typer.echo(_field("progress", f"{done}/{total} items"))

    try:
        outcome = triage_day(
            day,
            items,
            cfg,
            questions_cfg.questions,
            questions_cfg.aggregation,
            limit=limit,
            on_progress=progress,
        )
    except TypeSafeError as exc:
        _fail(
            f"the ranking engine refused the call: {exc}\n"
            "Check TYPESAFE_API_KEY in .env (see .env.example), then try again."
        )

    report = outcome.report
    if report.selected == 0:
        _fail(f"no enriched candidate for {day.isoformat()}: run `vbj enrich --date {day}` first.")

    typer.echo(f"vbj triage --date {day.isoformat()}")
    typer.echo(
        _field(
            "selection",
            f"{report.selected} of {report.candidates} enriched candidates (cap {cap})",
        )
    )
    typer.echo(
        _field(
            "answers",
            f"{report.answered} scored, {report.failed} failed, {report.passed} above "
            f"adjusted {cfg.min_adjusted_score}, {report.dropped} dropped",
        )
    )
    typer.echo(
        _field(
            "tokens",
            f"{report.input_tokens} input, {report.output_tokens} output, {report.duration_s} s",
        )
    )
    typer.echo(_field("written", f"{_display_path(out_path)} ({out_path.stat().st_size} bytes)"))
    typer.echo(
        _field(
            "cost",
            f"USD {report.cost_usd:.6f} — {report.input_tokens} input tokens at "
            f"USD {cfg.price_per_mtok_usd} per million",
        )
    )

    typer.echo("  top scores :")
    for score in sorted(outcome.scores, key=_preview_value, reverse=True)[:5]:
        verdict = "kept" if score.passed else ("failed" if score.error else "dropped")
        spread = "n/a" if score.spread is None else f"{score.spread:.2f}"
        typer.echo(
            f"    {_preview_value(score):>5.2f}  spread {spread:<4} {verdict:<7} {score.title[:52]}"
        )
