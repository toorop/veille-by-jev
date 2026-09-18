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

from pydantic import BaseModel, Field, model_validator

from veille.models import Window
from veille.store import CONFIG_DIR, ROOT

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
    window_hours: float = Field(
        default=24.0,
        gt=0,
        le=168,
        description="Length of the rolling window used when --date is not given.",
    )


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


class EnrichConfig(BaseModel):
    """Parameters of the enrichment stage.

    This is where the cap bounding the priced stages starts: `items.json` keeps the
    whole day, but only `max_items` candidates are fetched and truncated, and those
    are the ones triage will see.
    """

    max_items: int = Field(default=200, ge=1, description="Candidates enriched per night.")
    max_text_tokens: int = Field(default=2000, ge=1, description="Body text kept per article.")
    min_text_tokens: int = Field(
        default=200,
        ge=0,
        description="Below this, the extraction is treated as no text at all.",
    )
    max_comments: int = Field(default=5, ge=0, description="Top-level comments kept per item.")
    max_comment_tokens: int = Field(default=120, ge=1, description="Text kept per comment.")
    chars_per_token: float = Field(
        default=4.0, gt=0, description="Characters per token, for the token estimate."
    )
    timeout_s: float = Field(default=30.0, gt=0)


class SourcesConfig(BaseModel):
    """Whole content of `config/sources.toml`."""

    collect: CollectConfig = Field(default_factory=CollectConfig)
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)

    def enabled_sources(self) -> dict[str, SourceSettings]:
        """Return the enabled sources, keyed by their name in the config file."""
        return {name: cfg for name, cfg in self.sources.items() if cfg.enabled}


class TriageConfig(BaseModel):
    """Provider settings of the triage stage, from `config/questions.toml`."""

    model: str = Field(default="jev-latest", description="Model name passed to the engine.")
    score_penalty_z: float = Field(
        default=1.0,
        ge=0.0,
        description="Standard deviations of downside subtracted from a score before ranking.",
    )
    min_adjusted_score: float = Field(
        default=2.0,
        description="Below this adjusted score, an item is dropped without discussion.",
    )
    price_per_mtok_usd: float = Field(
        default=0.042,
        ge=0.0,
        description="Input price per million tokens, for the printed cost.",
    )
    max_items: int = Field(default=200, ge=1, description="Candidates triaged per night.")
    timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0, description="Retries on a retryable failure.")


class QuestionSpec(BaseModel):
    """One typed question, as written in the configuration.

    The shape of `criteria` depends on `type`: an ordered list of described levels for a
    `score`, a table of option to description for a `choice`, unused for a `noul`.
    """

    type: Literal["score", "choice", "noul"]
    instructions: str = ""
    criteria: list[str] | dict[str, str] | None = None

    @model_validator(mode="after")
    def _check_criteria(self) -> QuestionSpec:
        if self.type == "score" and not isinstance(self.criteria, list):
            raise ValueError("a score question needs `criteria` as an ordered list of levels")
        if self.type == "choice" and not isinstance(self.criteria, dict):
            raise ValueError("a choice question needs `criteria` as a table of options")
        return self


class AggregationConfig(BaseModel):
    """How the answers become one ranking value, from `config/questions.toml`."""

    scale: float = Field(
        default=4.0,
        gt=0,
        description="Scale the aggregate is expressed on, matching a five-level Score.",
    )
    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Relative weight of each question; absent means recorded but unranked.",
    )

    def normalised(self) -> dict[str, float]:
        """Return the weights rescaled to sum to one, ignoring the unweighted questions."""
        total = sum(weight for weight in self.weights.values() if weight > 0)
        if total <= 0:
            return {}
        return {name: weight / total for name, weight in self.weights.items() if weight > 0}


class QuestionsConfig(BaseModel):
    """Whole content of `config/questions.toml`: the scoring grid."""

    triage: TriageConfig = Field(default_factory=TriageConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    questions: dict[str, QuestionSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_weights(self) -> QuestionsConfig:
        unknown = [name for name in self.aggregation.weights if name not in self.questions]
        if unknown:
            # A typo in the weights table would silently drop a question from the ranking.
            raise ValueError(f"weights name unknown questions: {', '.join(sorted(unknown))}")
        return self


class WriteConfig(BaseModel):
    """Parameters of the writing stage, from `config/write.toml`."""

    model: str = Field(default="google/gemini-2.5-flash", description="Provider model id.")
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Chat-completions base URL; any OpenAI-compatible endpoint works.",
    )
    digest_size: int = Field(default=15, ge=1, description="Items kept in the digest.")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=16000, ge=1, description="Ceiling on the answer.")
    timeout_s: float = Field(default=180.0, gt=0)
    prompt_path: str = Field(default="config/write-prompt.md")
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="French labels for choice values, used in the digest only.",
    )

    def prompt_file(self) -> Path:
        """Return the system prompt path, resolved against the project root."""
        candidate = Path(self.prompt_path)
        return candidate if candidate.is_absolute() else ROOT / candidate


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


def load_questions_config(path: Path | None = None) -> QuestionsConfig:
    """Load `config/questions.toml`, the scoring grid.

    Args:
        path: File to read; defaults to `config/questions.toml`.

    Returns:
        The validated grid.

    Raises:
        FileNotFoundError: If the configuration file does not exist.

    """
    config_path = path or (CONFIG_DIR / "questions.toml")
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return QuestionsConfig.model_validate(raw)


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


def civil_day(now: datetime, tz_name: str) -> date:
    """Return the civil day `now` falls in, in `tz_name`.

    Args:
        now: Instant to place on the calendar, timezone-aware.
        tz_name: IANA zone name of the calendar.

    Returns:
        The matching civil day.

    Raises:
        ValueError: If `tz_name` is not a known zone, or `now` is naive.

    """
    try:
        tz = ZoneInfo(tz_name)
    except KeyError as exc:  # pragma: no cover - depends on the system zone database
        raise ValueError(f"Unknown timezone: {tz_name!r}") from exc
    if now.tzinfo is None:
        raise ValueError("civil_day needs an aware datetime")
    return now.astimezone(tz).date()


def rolling_window(now: datetime, tz_name: str, hours: float = 24.0) -> Window:
    """Return the `[now - hours, now)` window, labelled by the civil day it ends in.

    The counterpart of `day_window`, for a watch that must never lag: the batch always
    covers the last `hours` and always ends at the moment of the run. The price is that
    the same label no longer means the same batch, so a rolling run is identified by its
    stored window rather than by its date — see the idempotence rule in `collect`.

    Args:
        now: Instant the window ends at, timezone-aware.
        tz_name: IANA zone name used to label the run.
        hours: Length of the window, in hours.

    Returns:
        The matching half-open UTC window.

    Raises:
        ValueError: If `tz_name` is not a known zone, or `now` is naive.

    """
    try:
        tz = ZoneInfo(tz_name)
    except KeyError as exc:  # pragma: no cover - depends on the system zone database
        raise ValueError(f"Unknown timezone: {tz_name!r}") from exc
    if now.tzinfo is None:
        raise ValueError("rolling_window needs an aware datetime")

    end = now.astimezone(UTC)
    return Window(
        day=end.astimezone(tz).date(),
        timezone=tz_name,
        start=end - timedelta(hours=hours),
        end=end,
    )


def load_write_config(path: Path | None = None) -> WriteConfig:
    """Load `config/write.toml`.

    Args:
        path: File to read; defaults to `config/write.toml`.

    Returns:
        The validated settings.

    Raises:
        FileNotFoundError: If the configuration file does not exist.

    """
    config_path = path or (CONFIG_DIR / "write.toml")
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return WriteConfig.model_validate(raw.get("write", {}))
