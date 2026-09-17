"""Shared models for the pipeline stages.

These structures are the contract between commands: they are written to the files
under `data/` and read back by the next stage. Reshaping one of them is therefore
an interface change, not an internal detail.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, Literal

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


class Comment(BaseModel):
    """One discussion comment kept as part of the state."""

    model_config = ConfigDict(frozen=True)

    id: str
    author: str | None = None
    text: str
    published_at: datetime


class EnrichedItem(BaseModel):
    """Enrichment result for one item, cached under a URL fingerprint.

    Deliberately free of source metadata (points, comment count): those live in
    `items.json` and drift over time, while this file is a cache of what the article
    itself said. A score change must not invalidate the cache.

    Status `unavailable` means triage must not rely on the text: either the article
    could not be read, or it extracted to less than the configured floor. Whatever
    text was found is still kept, so nothing is lost and nothing is fetched twice.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    url: str
    fingerprint: str
    status: Literal["ok", "unavailable"]
    fetched_at: datetime
    title: str = ""
    text: str = Field(default="", description="Main text, already truncated.")
    text_tokens: int = Field(default=0, description="Estimated tokens of `text`.")
    text_truncated: bool = Field(default=False, description="Whether the article was cut.")
    comments: list[Comment] = Field(default_factory=list)
    error: str | None = Field(default=None, description="Why the text is unavailable.")
    comments_error: str | None = Field(
        default=None, description="Why the thread is missing, when the text is not."
    )


class EnrichReport(BaseModel):
    """What one enrichment run did, as printed at the end of the command."""

    candidates: int = Field(default=0, description="Candidates available in items.json.")
    selected: int = Field(default=0, description="Candidates inside the cap.")
    cached: int = Field(default=0, description="Items served from the cache.")
    fetched: int = Field(default=0, description="Items fetched from the network.")
    with_text: int = Field(default=0, description="Selected items that have article text.")
    unavailable: int = Field(default=0, description="Selected items without usable text.")
    comments_kept: int = 0
    thread_failures: int = Field(default=0, description="Items whose thread was not read.")
    requests: int = 0
    text_tokens: int = Field(default=0, description="Estimated tokens written, selected only.")
    duration_s: float = 0.0


class AnswerRecord(BaseModel):
    """One typed answer, normalised so the triage engine stays replaceable.

    The engine's own answer objects never leave the adapter: triage stores this shape
    instead, and a local ranking model would only have to produce the same fields.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    type: Literal["score", "choice", "noul"]
    value: float | str = Field(description="Score position, chosen option, or noul probability.")
    confidence: float | None = Field(
        default=None, description="Absent for a noul, whose value already is the belief."
    )
    probabilities: dict[str, float] = Field(default_factory=dict)
    legend: dict[str, Any] = Field(
        default_factory=dict, description="Level labels of a score, kept as the engine echoed them."
    )


class TriageUsage(BaseModel):
    """What one engine call cost, as the provider reported it.

    The SDK exposes only these two counters: the protocol also carries a `billing_units`
    field, but the public response drops it, so the printed cost applies the price list to
    `input_tokens` and the first invoice is what will confirm it.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None


class EngineAnswer(BaseModel):
    """What one engine call produced.

    The usage is carried even when reading an answer failed locally: a call that was made
    is a call that gets billed, whether or not this code managed to parse it.
    """

    answers: list[AnswerRecord] = Field(default_factory=list)
    usage: TriageUsage = Field(default_factory=TriageUsage)
    error: str | None = Field(
        default=None, description="Why an answer could not be read, when that happened."
    )


class ItemScore(BaseModel):
    """Everything triage concluded about one item."""

    item_id: str
    url: str
    title: str
    points: int = 0
    num_comments: int = 0
    answers: list[AnswerRecord] = Field(default_factory=list)
    confidence: float | None = Field(
        default=None, description="Weakest confidence among the answers, kept as a diagnostic."
    )
    adjusted: float | None = Field(
        default=None, description="Ranking value: score minus the configured downside penalty."
    )
    spread: float | None = Field(
        default=None, description="Standard deviation of the level distribution behind it."
    )
    passed: bool = Field(default=False, description="Whether it cleared the adjusted threshold.")
    error: str | None = None


class TriageReport(BaseModel):
    """What one triage run did, as printed at the end of the command."""

    candidates: int = Field(default=0, description="Enriched candidates available.")
    selected: int = Field(default=0, description="Candidates inside the cap.")
    answered: int = 0
    failed: int = 0
    passed: int = Field(default=0, description="Items whose confidence cleared the threshold.")
    dropped: int = Field(default=0, description="Items dropped by the confidence threshold.")
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    model: str = ""


class TriageOutcome(BaseModel):
    """Result of one triage run: what it cost, and what it concluded per item."""

    report: TriageReport
    scores: list[ItemScore] = Field(default_factory=list)


def deduplicate(items: Iterable[Item]) -> list[Item]:
    """Keep the best item per URL, sorted by descending score.

    The same article can be posted twice on one source, and two sources can point at
    the same URL. The winner is decided by `Item.outranks`, so a single rule governs
    both the per-source and the cross-source case.
    """
    best_by_url: dict[str, Item] = {}
    for item in items:
        current = best_by_url.get(item.url)
        if current is None or item.outranks(current):
            best_by_url[item.url] = item

    return sorted(
        best_by_url.values(),
        key=lambda item: (item.points, item.num_comments, item.published_at),
        reverse=True,
    )
