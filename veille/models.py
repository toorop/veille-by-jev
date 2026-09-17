"""Shared models for the pipeline stages.

These structures are the contract between commands: they are written to the files
under `data/` and read back by the next stage. Reshaping one of them is therefore
an interface change, not an internal detail.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Window(BaseModel):
    """Time window covered by a single collection run.

    `start` and `end` are UTC and always form a half-open interval `[start, end)`:
    a story published exactly at `end` belongs to the next day. This is what makes
    a given date yield the same batch, whenever the run happens.
    """

    model_config = ConfigDict(frozen=True)

    day: date = Field(description="Civil day covered, in the `timezone` zone.")
    timezone: str = Field(description="Zone used to cut the civil day.")
    start: datetime = Field(description="Window start, in UTC.")
    end: datetime = Field(description="Window end, excluded, in UTC.")

    @property
    def hours(self) -> float:
        """Return the window length in hours."""
        return (self.end - self.start).total_seconds() / 3600


class Item(BaseModel):
    """One collected item, before enrichment.

    The fields are deliberately thin: only what the source can state without
    reading the article. Body text and comments arrive at stage 2.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable identifier, prefixed by the source: `hn:<objectID>`.")
    source: str = Field(description="Short source name: `hn`.")
    title: str
    url: str = Field(description="Article URL, or the discussion thread when the item has none.")
    discussion_url: str = Field(description="Discussion thread URL on the source.")
    author: str | None = None
    points: int = Field(ge=0, description="Source score (Hacker News points).")
    num_comments: int = Field(ge=0)
    published_at: datetime = Field(description="Publication timestamp, in UTC.")
    kind: str = Field(
        default="story",
        description="Kind of item on the source side: `story`, `show_hn`, `ask_hn`…",
    )

    def outranks(self, other: Item) -> bool:
        """Return True when this item should win a deduplication against `other`.

        The tie-break is deterministic: points first, then comment count. Keeping
        the rule here means the pipeline and the sources cannot disagree on it.
        """
        return (self.points, self.num_comments) > (other.points, other.num_comments)


class SourceReport(BaseModel):
    """What one source did during collection, including when it failed.

    A source failure is recorded here and must not bring the stage down.
    """

    source: str
    label: str = ""
    status: Literal["ok", "error"]
    requests: int = 0
    fetched: int = Field(default=0, description="Raw items returned by the source.")
    kept: int = Field(default=0, description="Items kept after score filtering.")
    duration_s: float = 0.0
    error: str | None = None


class CollectOutcome(BaseModel):
    """Outcome of one source: its items plus its execution report."""

    items: list[Item] = Field(default_factory=list)
    report: SourceReport
