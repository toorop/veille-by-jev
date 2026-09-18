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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from typesafe_sdk import TypeSafeError

from veille import __version__
from veille.clients.openrouter import ChatError
from veille.config import (
    QuestionsConfig,
    SourcesConfig,
    WriteConfig,
    civil_day,
    day_window,
    load_questions_config,
    load_sources_config,
    load_write_config,
    rolling_window,
)
from veille.enrich import enrich_day
from veille.env import load_dotenv
from veille.models import CollectOutcome, Item, ItemScore, SourceReport, Window, deduplicate
from veille.sources import hn
from veille.store import (
    ROOT,
    digest_path,
    enriched_dir,
    iso_utc,
    items_path,
    read_json,
    scores_path,
    write_json,
)
from veille.triage import triage_day
from veille.write import prepare_prompt, write_day

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="veille-by-jev — night watch over Hacker News, turned into a French Markdown digest.",
)

SOURCE_MODULES = {"hn": hn}

FIELD_WIDTH = 10


@dataclass(frozen=True)
class StageResult:
    """What one stage did, so a full run can total it.

    Attributes:
        stage: Stage name, as the reader knows it (`collect`, `enrich`, …).
        skipped: True when the stage found its work already done and spent nothing.
        cost_usd: What this run spent on the stage, or None when the provider did not
            report it.
        already_usd: What an earlier run already spent for the same window, so a skipped
            stage never looks free when the money went out earlier.
        conflict: True when the stage found a *different* window stored under the same
            label, which a full run must not walk past in silence.
    """

    stage: str
    skipped: bool
    cost_usd: float | None = 0.0
    already_usd: float = 0.0
    conflict: bool = False


@dataclass(frozen=True)
class CollectionPlan:
    """The day a run works on, the window it covers, and how that was decided.

    Attributes:
        day: Label of the run, used for every file path.
        window: Window the collection covers.
        explicit: True when `--date` was given rather than resolved from the clock.
    """

    day: date
    window: Window
    explicit: bool

    def headline(self, command: str) -> str:
        """Name the run in the end-of-run report."""
        return _headline(command, self.day, self.explicit)


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


def _headline(command: str, day: date, explicit: bool) -> str:
    """Name the run in the end-of-run report, saying whether the date was given or defaulted."""
    if explicit:
        return f"vbj {command} --date {day.isoformat()}"
    return f"vbj {command} ({day.isoformat()}, no --date)"


def _sources_settings() -> SourcesConfig:
    """Load `config/sources.toml`, exiting with a clear message when it cannot be read."""
    try:
        return load_sources_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/sources.toml:\n{exc}")


def _plan_collection(raw_date: str | None, settings: SourcesConfig) -> CollectionPlan:
    """Decide once what a collection covers, so every stage of a run agrees.

    Resolving the day in one place is what lets `vbj run` stay correct across midnight:
    with the civil-day mode the two forms coincide, and with the rolling one the window is
    computed at the start and handed to the rest of the run rather than recomputed.
    """
    if raw_date:
        day = _parse_day(raw_date)
        return CollectionPlan(day, day_window(day, settings.collect.timezone), True)
    window = rolling_window(
        datetime.now(UTC), settings.collect.timezone, settings.collect.window_hours
    )
    return CollectionPlan(window.day, window, False)


def _resolve_day(raw: str | None) -> date:
    """Return the day to work on: the `--date` value, or today in the configured zone.

    Omitting `--date` on a downstream stage means "the run happening now", which is the
    day `collect` would label a fresh rolling window with. A run that crosses midnight
    between two stages needs an explicit `--date`, and says so rather than silently
    working on the wrong day.
    """
    if raw:
        return _parse_day(raw)
    return civil_day(datetime.now(UTC), _sources_settings().collect.timezone)


def _version_callback(value: bool) -> None:
    """Print the version and exit, as an eager `--version` option."""
    if value:
        typer.echo(f"veille-by-jev {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    """Run the veille-by-jev pipeline, one subcommand per stage.

    With no subcommand at all, the whole night runs: `collect`, `enrich`, `triage`, then
    `write`, on the last `window_hours` ending now.
    """
    # Read .env before any stage runs, so that a locally stored API key is available
    # without a shell setup. A variable already exported wins over the file.
    load_dotenv()
    if ctx.invoked_subcommand is None:
        # `vbj` alone is the nightly cron line. It is safe to repeat: every stage skips
        # what is already done, and the one stage that pays does so at most once a window.
        full_run(None, force=False)


@app.command()
def run(
    raw_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Civil day to run, as YYYY-MM-DD. Omit for the last window_hours.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Redo every stage even when its output already exists."),
    ] = False,
) -> None:
    """Run the whole night: `collect`, `enrich`, `triage`, then `write`.

    The day and the window are decided once, at the start, and every stage works on
    exactly that: a run started before midnight cannot collect one day and write another.
    The stages are the same four commands, so everything they document holds here —
    each one skips what is already done, `--force` redoes all of them, and the report
    ends with the total of what the night cost.

    A stage that fails stops the run: the ones after it depend on its output. What was
    already spent by then is still reported.
    """
    full_run(raw_date, force=force)


def full_run(raw_date: str | None, *, force: bool) -> None:
    """Run the four stages in order, stopping at the first failure.

    Args:
        raw_date: Civil day to run, or None for the rolling window.
        force: Redo every stage even when its output already exists.

    Raises:
        typer.Exit: When a stage fails, with that stage's exit code.
    """
    settings = _sources_settings()
    plan = _plan_collection(raw_date, settings)
    questions_cfg = _questions_config()
    write_cfg, grid = _write_configs()

    typer.echo(plan.headline("run"))
    results: list[StageResult] = []

    def guard(stage: str, call: Callable[[], StageResult]) -> StageResult:
        """Run one stage, reporting what was spent if it stops the night."""
        try:
            return call()
        except typer.Exit:
            _summarise(results, plan, failed=stage)
            raise

    results.append(
        guard(
            "collect",
            lambda: _collect(plan, settings, force=force),
        )
    )
    # A stored window that differs means the batch on disk is not the one this run asked
    # for. Continuing would produce a digest for the wrong hours while the report read as
    # "up to date", so the run stops and says why.
    if results[-1].conflict:
        _summarise(results, plan, failed="collect")
        typer.secho(
            _field("window", "the stored one differs; add --force to collect the new one"),
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    results.append(
        guard(
            "enrich",
            lambda: _enrich(
                plan.day,
                settings,
                limit=None,
                force=force,
                headline=plan.headline("enrich"),
            ),
        )
    )
    results.append(
        guard(
            "triage",
            lambda: _triage(
                plan.day,
                questions_cfg,
                limit=None,
                force=force,
                headline=plan.headline("triage"),
            ),
        )
    )
    results.append(
        guard(
            "write",
            lambda: _write(
                plan.day,
                write_cfg,
                grid,
                model=None,
                output=None,
                force=force,
                headline=plan.headline("write"),
            ),
        )
    )
    _summarise(results, plan)


def _questions_config() -> QuestionsConfig:
    """Load `config/questions.toml`, exiting with a clear message when it cannot be read."""
    try:
        return load_questions_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/questions.toml:\n{exc}")


def _summarise(
    results: list[StageResult],
    plan: CollectionPlan,
    *,
    failed: str | None = None,
) -> None:
    """Close a full run with what it did and what it cost, failure included."""
    typer.echo("  " + "-" * 46)
    done = [result for result in results if not result.skipped]
    skipped = [result for result in results if result.skipped]
    typer.echo(
        _field(
            "stages",
            f"{len(done)} ran, {len(skipped)} skipped"
            + (f", stopped at {failed}" if failed else ""),
        )
    )
    for result in results:
        state = "skipped" if result.skipped else "ran"
        earlier = f"  (USD {result.already_usd:.6f} spent earlier)" if result.already_usd else ""
        typer.echo(f"    {result.stage:<8} {state}{earlier}")

    destination = digest_path(plan.day)
    if destination.exists():
        typer.echo(
            _field(
                "digest",
                f"{_display_path(destination)} ({destination.stat().st_size} bytes)",
            )
        )
    reported = [r for r in results if r.cost_usd is not None]
    total = sum(r.cost_usd or 0.0 for r in reported)
    unknown = len(results) - len(reported)
    earlier_total = sum(r.already_usd for r in results)
    suffix = f", {unknown} stage(s) not reported by the provider" if unknown else ""
    typer.echo(_field("cost", f"USD {total:.6f} for this run{suffix}"))
    if earlier_total:
        typer.echo(_field("already", f"USD {earlier_total:.6f} for this window, earlier runs"))
    if failed:
        typer.secho(
            _field("stopped", f"nothing after {failed} was attempted"),
            err=True,
            fg=typer.colors.RED,
        )


@app.command()
def collect(
    raw_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Civil day to collect, as YYYY-MM-DD. Omit for the last window_hours.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Collect again even if data/<date>/items.json exists."),
    ] = False,
) -> None:
    """Stage 1 — collect the enabled sources into `data/<date>/items.json`.

    No model call at all: this stage is deliberately dumb and verifiable. A source
    failure is recorded in the output file and does not bring the command down.
    Running it again on a window already collected does nothing unless `--force` is
    passed.

    With `--date`, the window is that whole civil day: the same date always yields the
    same batch, which is what makes a run replayable and comparable. Without `--date`,
    the window is the last `window_hours` ending now, so the digest never lags — and
    because such a window differs on every run, what was already collected is identified
    by its stored window, not by its date.

    Output is deliberately uncapped: every candidate above the score floor is
    written, because collection costs a single request. The cap that bounds the
    priced stages is applied by the stage that pays for it.
    """
    settings = _sources_settings()
    plan = _plan_collection(raw_date, settings)
    _collect(plan, settings, force=force)


def _collect(plan: CollectionPlan, settings: SourcesConfig, *, force: bool) -> StageResult:
    """Run stage 1 and print its report.

    Args:
        plan: The day and window to collect, already decided.
        settings: Sources configuration.
        force: Collect again even when the window is already stored.

    Returns:
        What the stage did, for the total of a full run.

    Raises:
        typer.Exit: When a source fails or nothing passes the score floor.
    """
    day, window = plan.day, plan.window
    out_path = items_path(day)

    # Idempotence invariant: without --force, never redo work already done. A rolling
    # window is new on every run, so what is checked is the stored window, not the file.
    if out_path.exists() and not force:
        existing = read_json(out_path)
        stored = existing.get("window") or {}
        wanted_start, wanted_end = iso_utc(window.start), iso_utc(window.end)
        already = stored.get("start") == wanted_start and stored.get("end") == wanted_end
        stats = existing.get("stats", {})
        collected_at = existing.get("generated_at", "?")
        typer.echo(f"{_display_path(out_path)} already exists — nothing to do.")
        typer.echo(
            _field("items", f"{stats.get('candidates', '?')} (collected at {collected_at})")
        )
        if already:
            typer.echo(_field("window", f"{wanted_start} → {wanted_end} — the requested one"))
        else:
            typer.echo(
                _field(
                    "stored",
                    f"{stored.get('start', '?')} → {stored.get('end', '?')} "
                    f"({stored.get('hours', '?')} h)",
                )
            )
            typer.echo(
                _field(
                    "wanted",
                    f"{wanted_start} → {wanted_end} ({window.hours:g} h), "
                    "which differs from the stored one",
                )
            )
        typer.echo(_field("path", str(out_path)))
        typer.echo(_field("re-run", "add --force to collect again"))
        typer.echo(_field("cost", "USD 0.00 (no model call)"))
        return StageResult("collect", skipped=True, conflict=not already)

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
    typer.echo(plan.headline("collect"))
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
    return StageResult("collect", skipped=False)


@app.command()
def enrich(
    raw_date: Annotated[
        str | None,
        typer.Option("--date", help="Civil day to enrich, as YYYY-MM-DD. Omit for today."),
    ] = None,
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
    day = _resolve_day(raw_date)
    _enrich(
        day,
        _sources_settings(),
        limit=limit,
        force=force,
        headline=_headline("enrich", day, raw_date is not None),
    )


def _enrich(
    day: date,
    settings: SourcesConfig,
    *,
    limit: int | None,
    force: bool,
    headline: str,
) -> StageResult:
    """Run stage 2 and print its report.

    Args:
        day: Day to enrich.
        settings: Sources configuration.
        limit: Enrich at most this many candidates, instead of the configured cap.
        force: Fetch again even when a cache file exists.
        headline: First line of the report.

    Returns:
        What the stage did, for the total of a full run.

    Raises:
        typer.Exit: When `items.json` is missing.
    """
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

    typer.echo(headline)
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
    # No file marks this stage done: its cache does. Nothing fetched means nothing to do.
    return StageResult("enrich", skipped=report.fetched == 0)


@app.command()
def triage(
    raw_date: Annotated[
        str | None,
        typer.Option("--date", help="Civil day to score, as YYYY-MM-DD. Omit for today."),
    ] = None,
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
    day = _resolve_day(raw_date)
    try:
        questions_cfg = load_questions_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/questions.toml:\n{exc}")
    _triage(
        day,
        questions_cfg,
        limit=limit,
        force=force,
        headline=_headline("triage", day, raw_date is not None),
    )


def _triage(
    day: date,
    questions_cfg: QuestionsConfig,
    *,
    limit: int | None,
    force: bool,
    headline: str,
) -> StageResult:
    """Run stage 3 and print its report.

    Args:
        day: Day to score.
        questions_cfg: The grid and the aggregation settings.
        limit: Score at most this many candidates, instead of the configured cap.
        force: Score again even when `scores.json` already exists.
        headline: First line of the report.

    Returns:
        What the stage did, for the total of a full run.

    Raises:
        typer.Exit: When the engine refuses, or there is nothing to score.
    """
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
        # This run spent nothing here, but the window is not free: the total says both.
        spent = stats.get("cost_usd")
        return StageResult("triage", skipped=True, already_usd=float(spent) if spent else 0.0)

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

    typer.echo(headline)
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
    return StageResult("triage", skipped=False, cost_usd=report.cost_usd)


@app.command()
def write(
    raw_date: Annotated[
        str | None,
        typer.Option("--date", help="Civil day to write up, as YYYY-MM-DD. Omit for today."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the configured writer, to compare models."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write elsewhere, so a comparison keeps the digest."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Write again even when the digest already exists."),
    ] = False,
    dump_prompt: Annotated[
        Path | None,
        typer.Option(
            "--dump-prompt",
            help="Write the exact user prompt to this path and stop, without calling a model.",
        ),
    ] = None,
) -> None:
    """Stage 4 — write the French Markdown digest into `digest/<date>.md`.

    Takes the items triage admitted, sends their state to the writing model in one call,
    and assembles the digest from the answer. Everything structural — dates, links,
    sources, scores, the set-aside table and the cost line — is generated from the data, so
    the model only writes prose and can quote no figure of its own.

    The system prompt of `config/write-prompt.md` carries the readability requirement: a
    non-specialist must understand every sentence on the first pass.

    `--model` and `--output` exist to compare two writers on the same night without
    overwriting the digest.
    """
    day = _resolve_day(raw_date)
    cfg, grid = _write_configs()
    headline = _headline("write", day, raw_date is not None)

    if dump_prompt is not None:
        _dump_prompt(day, cfg, grid, dump_prompt, headline)
        return

    _write(day, cfg, grid, model=model, output=output, force=force, headline=headline)


def _write_configs() -> tuple[WriteConfig, QuestionsConfig]:
    """Load the writing settings and the grid, exiting with a clear message otherwise."""
    try:
        cfg = load_write_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"invalid config/write.toml:\n{exc}")
    try:
        grid = load_questions_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    return cfg, grid


def _dump_prompt(
    day: date,
    cfg: WriteConfig,
    grid: QuestionsConfig,
    destination: Path,
    headline: str,
) -> None:
    """Write the exact state a run would send, without reading a key or calling anything.

    Raises:
        typer.Exit: When `scores.json` is missing.
    """
    try:
        prepared = prepare_prompt(day, cfg, grid)
    except FileNotFoundError as exc:
        _fail(str(exc))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(prepared.user_prompt, encoding="utf-8")
    typer.echo(headline + " --dump-prompt")
    typer.echo(
        _field(
            "prompt",
            f"{len(prepared.user_prompt)} characters, {len(prepared.kept)} items, "
            f"floor {prepared.floor}, digest size {cfg.digest_size}",
        )
    )
    typer.echo(_field("written", f"{_display_path(destination)}"))
    typer.echo(_field("system", f"{_display_path(cfg.prompt_file())}"))
    typer.echo(_field("cost", "USD 0.00 — no model call"))


def _write(
    day: date,
    cfg: WriteConfig,
    grid: QuestionsConfig,
    *,
    model: str | None,
    output: Path | None,
    force: bool,
    headline: str,
) -> StageResult:
    """Run stage 4 and print its report.

    Args:
        day: Day to write up.
        cfg: Writing settings.
        grid: The grid, for the admission floor and the question names.
        model: Overrides the configured writer, for comparing writers.
        output: Writes elsewhere, so a comparison keeps the digest.
        force: Write again even when the digest already exists.
        headline: First line of the report.

    Returns:
        What the stage did, for the total of a full run.

    Raises:
        typer.Exit: When the provider refuses, or `scores.json` is missing.
    """
    destination = output or digest_path(day)
    if destination.exists() and not force:
        typer.echo(f"{_display_path(destination)} already exists — nothing to do.")
        typer.echo(_field("re-run", "add --force to write it again"))
        return StageResult("write", skipped=True)

    try:
        outcome = write_day(day, cfg, grid, model=model, output=output, force=force)
    except ChatError as exc:
        _fail(f"the writing model refused the call: {exc}")
    except FileNotFoundError as exc:
        _fail(str(exc))

    report = outcome.report
    typer.echo(headline)
    typer.echo(
        _field(
            "selection",
            f"{report.kept} written up, {report.dropped} set aside, "
            f"out of {report.considered} triaged (floor {report.floor})",
        )
    )
    typer.echo(
        _field(
            "model",
            f"{report.model} — {report.input_tokens} input, {report.output_tokens} output, "
            f"{report.duration_s} s",
        )
    )
    cost = "not reported by the provider" if report.cost_usd is None else f"USD {report.cost_usd}"
    typer.echo(_field("cost", cost))
    typer.echo(
        _field("written", f"{_display_path(destination)} ({destination.stat().st_size} bytes)")
    )
    if report.warning:
        typer.secho(_field("warning", report.warning), err=True, fg=typer.colors.YELLOW)

    typer.echo("  digest :")
    for line in outcome.markdown.splitlines()[:14]:
        typer.echo(f"    {line}")
    return StageResult("write", skipped=False, cost_usd=report.cost_usd)
