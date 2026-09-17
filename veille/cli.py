"""Command-line interface of the pipeline.

One command per stage, with files as the interface: `collect`, then `enrich`,
`triage` and `write` (later stages). Each command can be replayed on its own for a
given date, and replaying a finished stage costs nothing unless `--force` is passed.

Language convention: comments and docstrings are English, like the rest of the
code. Everything the operator reads — help, progress, errors — is French, like the
digest itself.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from veille import __version__
from veille.config import day_window, load_sources_config
from veille.models import CollectOutcome, Item, SourceReport
from veille.sources import hn
from veille.store import ROOT, items_path, read_json, write_json

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="veille-by-jev — pipeline de veille Hacker News vers un digest Markdown en français.",
)

SOURCE_MODULES = {"hn": hn}


def _fail(message: str, code: int = 2) -> NoReturn:
    """Print an error on stderr and exit with `code`."""
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(code=code)


def _parse_day(raw: str) -> date:
    """Parse an `AAAA-MM-JJ` option value, exiting with a clear message otherwise."""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        _fail(f"Date invalide : {raw!r}. Format attendu : AAAA-MM-JJ (exemple : 2026-09-16).")


def _iso(moment: datetime) -> str:
    """Format an instant as an explicit UTC ISO 8601 string ending in `Z`."""
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            "--version", callback=_version_callback, is_eager=True, help="Affiche la version."
        ),
    ] = False,
) -> None:
    """Run the veille-by-jev pipeline, one subcommand per stage."""


@app.command(
    help=(
        "Étape 1 — collecte les items des sources activées dans data/<date>/items.json.\n\n"
        "Aucun appel à un modèle : cette étape est volontairement bête et vérifiable. "
        "Un échec de source est journalisé dans le fichier et ne fait pas tomber la commande."
    )
)
def collect(
    raw_date: Annotated[
        str, typer.Option("--date", help="Journée civile à collecter, format AAAA-MM-JJ.")
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Recollecte même si data/<date>/items.json existe déjà."),
    ] = False,
) -> None:
    """Collect the items of every enabled source into `data/<date>/items.json`.

    Stage 1. No model call at all: this stage is deliberately dumb and verifiable.
    A source failure is recorded in the output file and does not bring the command
    down. Running it again on a date already collected is a no-op unless `--force`
    is passed.
    """
    day = _parse_day(raw_date)
    try:
        settings = load_sources_config()
    except FileNotFoundError as exc:
        _fail(str(exc))
    except ValidationError as exc:  # pragma: no cover - depends on the file being edited
        _fail(f"config/sources.toml invalide :\n{exc}")

    window = day_window(day, settings.collect.timezone)
    out_path = items_path(day)

    # Idempotence invariant: without --force, never redo work already done.
    if out_path.exists() and not force:
        existing = read_json(out_path)
        stats = existing.get("stats", {})
        already_selected = stats.get("selected", "?")
        collected_at = existing.get("generated_at", "?")
        typer.echo(f"{out_path.relative_to(ROOT)} existe déjà — rien à faire.")
        typer.echo(f"  items      : {already_selected} (collecte du {collected_at})")
        typer.echo(f"  source     : {out_path}")
        typer.echo("  pour refaire la collecte : ajouter --force")
        typer.echo("  coût       : 0,00 USD (aucun appel de modèle)")
        return

    enabled = settings.enabled_sources()
    if not enabled:
        _fail("Aucune source activée dans config/sources.toml.")

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
                        error=f"type de source non implémenté : {source_cfg.type!r}",
                    )
                )
            )
            continue
        outcomes.append(module.collect(window, source_cfg, settings.collect, name))
    duration_s = round(time.monotonic() - started, 2)

    # Merge and deduplicate across sources, then truncate once to target_items.
    best_by_url: dict[str, Item] = {}
    for outcome in outcomes:
        for item in outcome.items:
            current = best_by_url.get(item.url)
            if current is None or item.outranks(current):
                best_by_url[item.url] = item
    merged = sorted(
        best_by_url.values(),
        key=lambda item: (item.points, item.num_comments, item.published_at),
        reverse=True,
    )
    selected = merged[: settings.collect.target_items]
    fetched = sum(outcome.report.fetched for outcome in outcomes)
    after_filter = sum(outcome.report.kept for outcome in outcomes)

    # --- Report: the per-source lines come before any decision, so that an empty
    # run reads either as "source down" or as "quiet day" ---
    typer.echo(f"vbj collect --date {day.isoformat()}")
    utc_range = f"{_iso(window.start)} → {_iso(window.end)}"
    typer.echo(f"  fuseau    : {window.timezone} — fenêtre UTC {utc_range}")
    for outcome in outcomes:
        report = outcome.report
        if report.status == "ok":
            typer.echo(
                f"  {report.source:<9} : ok — {report.fetched} items bruts, "
                f"{report.kept} après filtre points >= {settings.collect.min_points}, "
                f"{report.requests} requête(s), {report.duration_s} s"
            )
        else:
            typer.secho(
                f"  {report.source:<9} : échec — {report.error}", err=True, fg=typer.colors.YELLOW
            )

    if not selected:
        typer.echo(f"  sélection : {after_filter} après filtre → 0 retenu")
        typer.echo("  coût      : 0,00 USD — aucun appel de modèle à cette étape")
        _fail(
            f"Rien à écrire pour le {day.isoformat()} : source en échec ou fenêtre "
            f"sans item au-dessus de min_points={settings.collect.min_points} "
            "(voir le rapport ci-dessus).",
            code=1,
        )

    published = [item.published_at for item in selected]
    payload = {
        "schema_version": 1,
        "date": day.isoformat(),
        "timezone": window.timezone,
        "generated_at": _iso(datetime.now(UTC)),
        "window": {"start": _iso(window.start), "end": _iso(window.end), "hours": window.hours},
        "settings": {
            "target_items": settings.collect.target_items,
            "min_points": settings.collect.min_points,
        },
        "sources": [outcome.report.model_dump(mode="json") for outcome in outcomes],
        "stats": {
            "fetched": fetched,
            "after_score_filter": after_filter,
            "after_cross_source_dedup": len(merged),
            "selected": len(selected),
            "truncated": max(0, len(merged) - len(selected)),
            "published_at_min": _iso(min(published)),
            "published_at_max": _iso(max(published)),
            "duration_s": duration_s,
            "model_calls": 0,
            "cost_usd": 0.0,
        },
        "items": [item.model_dump(mode="json") for item in selected],
    }
    write_json(out_path, payload)

    typer.echo(
        f"  sélection : {after_filter} après filtre → {len(merged)} après déduplication "
        f"→ {len(selected)} retenus (cible {settings.collect.target_items})"
    )
    typer.echo(f"  écrit     : {out_path.relative_to(ROOT)} ({out_path.stat().st_size} octets)")
    typer.echo(f"  publiés   : {_iso(min(published))} → {_iso(max(published))}")
    typer.echo("  coût      : 0,00 USD — aucun appel de modèle à cette étape")
