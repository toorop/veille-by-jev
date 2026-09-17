"""Pipeline configuration: reading `config/*.toml` and computing windows.

Project rule: the scoring grid and its parameters live in TOML files, and changing
the configuration must never require touching code.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from veille.models import Window
from veille.store import CONFIG_DIR

# Algolia API ceiling: pagination stops past 1000 results. One request at that
# ceiling therefore covers a whole Hacker News day.
MAX_HITS_PER_QUERY = 1000


class CollectConfig(BaseModel):
    """Parameters shared by every source.

    There is no item cap here on purpose: collection costs nothing beyond a single
    HTTP request, so it keeps every candidate above `min_points`. The cap that
    bounds the priced stages belongs to the stage that pays, and is set where it is
    consumed (`enrich`, then `triage`).
    """

    timezone: str = Field(default="Europe/Paris", description="Zone of the civil days.")
    min_points: int = Field(default=1, ge=0, description="Minimum source score to be kept.")


class SourceSettings(BaseModel):
    """One source declared in `config/sources.toml`."""

    enabled: bool = True
    type: Literal["hn"] = "hn"
    label: str = ""
    endpoint: str
    tags: str = "story"
    hits_per_page: int = Field(default=MAX_HITS_PER_QUERY, ge=1, le=MAX_HITS_PER_QUERY)
    max_pages: int = Field(default=1, ge=1, le=10)
    timeout_s: float = Field(default=30.0, gt=0)


class SourcesConfig(BaseModel):
    """Whole content of `config/sources.toml`."""

    collect: CollectConfig = Field(default_factory=CollectConfig)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)

    def enabled_sources(self) -> dict[str, SourceSettings]:
        """Return the enabled sources, keyed by their name in the config file."""
        return {name: cfg for name, cfg in self.sources.items() if cfg.enabled}


def load_sources_config(path: Path | None = None) -> SourcesConfig:
    """Load `config/sources.toml`.

    Args:
        path: File to read; defaults to `config/sources.toml`.

    Returns:
        The validated configuration.

    Raises:
        FileNotFoundError: If the configuration file does not exist.

    """
    config_path = path or (CONFIG_DIR / "sources.toml")
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return SourcesConfig.model_validate(raw)


def day_window(day: date, tz_name: str) -> Window:
    """Return the `[day 00:00, day+1 00:00)` window of `tz_name`, expressed in UTC.

    A civil-day window rather than "the last 24 hours": the same `--date` always
    yields the same batch, whatever time the run happens.

    Args:
        day: Civil day to cover.
        tz_name: IANA zone name used to cut that day.

    Returns:
        The matching half-open UTC window.

    Raises:
        ValueError: If `tz_name` is not a known zone.

    """
    try:
        tz = ZoneInfo(tz_name)
    except KeyError as exc:  # pragma: no cover - depends on the system zone database
        raise ValueError(f"Unknown timezone: {tz_name!r}") from exc

    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return Window(day=day, timezone=tz_name, start=start, end=end)
