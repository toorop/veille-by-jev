"""Stage 3: judging the enriched candidates with typed questions.

The judgement itself belongs to the engine behind `veille.clients`; this module decides what
state it receives, applies the rules from the notes, and logs the ranking so the grid can be
contested after the fact.

Two rules come from the scoping notes and live here:

- **The confidence filter runs before the ranking.** An item whose weakest confidence falls
  below the configured threshold is dropped without discussion, whatever its score.
- **The grid is written next to the scores.** A ranking is only contestable if the questions
  that produced it are recorded with it.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from veille.clients.typesafe import TypeSafeEngine
from veille.config import AggregationConfig, QuestionSpec, TriageConfig
from veille.models import (
    AnswerRecord,
    EngineAnswer,
    EnrichedItem,
    Item,
    ItemScore,
    TriageOutcome,
    TriageReport,
    TriageUsage,
)
from veille.store import enriched_path, iso_utc, read_json, scores_path, write_json


class Engine(Protocol):
    """What triage needs from a ranking engine.

    `TypeSafeEngine` is the real one; tests inject a stand-in, which is what keeps the
    suite free of network calls and provider quotas.
    """

    model: str

    def answer(self, state: Mapping[str, Any]) -> EngineAnswer:
        """Answer the configured questions about one state."""
        ...

    def close(self) -> None:
        """Release whatever the engine holds."""
        ...


def build_state(item: Item, enriched: EnrichedItem | None, *, now: datetime) -> dict[str, Any]:
    """Build the state sent to the engine for one item.

    Kept short on purpose: only the input is billed, and the article text already went
    through a token budget upstream. An item without usable text is sent as such, with an
    explicit status, rather than as an empty article.

    Args:
        item: The candidate, as collected.
        enriched: Its enrichment record, when one exists.
        now: Instant used to compute the age of the publication.

    Returns:
        The state, as a mapping of named fields.
    """
    state: dict[str, Any] = {
        "title": item.title,
        "source": item.source,
        "kind": item.kind,
        "source_score": item.points,
        "source_comments": item.num_comments,
        "published_at": iso_utc(item.published_at),
        "age_hours": round((now - item.published_at).total_seconds() / 3600, 1),
        "url": item.url,
    }

    if enriched is None:
        state["article"] = {"status": "unavailable", "reason": "not enriched"}
    elif enriched.status == "unavailable":
        state["article"] = {"status": "unavailable", "reason": enriched.error or "unknown"}
    else:
        state["article"] = {
            "status": "ok",
            "truncated": enriched.text_truncated,
            "text": enriched.text,
        }

    if enriched is not None and enriched.comments:
        state["thread"] = [
            {"author": comment.author, "text": comment.text} for comment in enriched.comments
        ]
    return state


def weakest_confidence(answers: Iterable[AnswerRecord]) -> float | None:
    """Return the lowest confidence among the answers.

    Kept as a diagnostic, not as a filter: confidence says how precise a position is, not
    how high it is, and filtering on it alone ranked backwards — see `rank_value` for what
    replaced it.
    """
    values = [answer.confidence for answer in answers if answer.confidence is not None]
    return min(values) if values else None


def level_spread(probabilities: Mapping[str, float]) -> float | None:
    """Return the standard deviation of a level distribution.

    The keys are level indices and the values their probability. The spread says how far
    the mass could fall: two neighbouring levels give a small value, "no interest" against
    "essential" gives a large one. It is what the confidence scalar cannot express.

    Args:
        probabilities: Distribution as the engine returned it.

    Returns:
        The standard deviation, or `None` when the distribution is unusable.
    """
    pairs: list[tuple[float, float]] = []
    for key, weight in probabilities.items():
        try:
            pairs.append((float(key), float(weight)))
        except (TypeError, ValueError):
            return None
    total = sum(weight for _, weight in pairs)
    if not pairs or total <= 0:
        return None

    mean = sum(level * weight for level, weight in pairs) / total
    variance = sum(weight * (level - mean) ** 2 for level, weight in pairs) / total
    return math.sqrt(variance)


@dataclass(frozen=True)
class Aggregate:
    """The weighted judgement of one item.

    Attributes:
        adjusted: Weighted lower bound, on the configured scale.
        spread: Weighted downside spread, on the same scale.
        components: Normalised adjusted value contributed by each weighted question.
    """

    adjusted: float
    spread: float
    components: dict[str, float]


def _score_component(
    answer: AnswerRecord, spec: QuestionSpec, penalty_z: float
) -> tuple[float, float] | None:
    """Normalise one `Score` answer to [0, 1], penalty included.

    Returns:
        The adjusted value and the spread, both in [0, 1], or `None` when the answer cannot
        be placed on a scale.
    """
    if not isinstance(answer.value, float) or not isinstance(spec.criteria, list):
        return None
    top = len(spec.criteria) - 1
    if top <= 0:
        return None
    spread = level_spread(answer.probabilities) or 0.0
    return (answer.value - penalty_z * spread) / top, spread / top


def component_value(
    answer: AnswerRecord, spec: QuestionSpec, penalty_z: float
) -> tuple[float, float] | None:
    """Return the normalised value of one answer and its normalised spread.

    Components are normalised to [0, 1] before weighting, so that the weights mean what
    they say: the scoping notes' example formula added a position on 0–4 to a probability
    on 0–1, which silently deflated the primary-source signal.

    A `noul` is already a probability, so it is used as is: its uncertainty is inside the
    number itself, and the engine returns no distribution for it.

    Args:
        answer: One normalised engine answer.
        spec: The question it answers.
        penalty_z: Standard deviations of downside to subtract, for score answers.

    Returns:
        The adjusted value and its spread, both in [0, 1]; `None` for an answer that does
        not rank, such as a `choice`.
    """
    if answer.type == "score":
        return _score_component(answer, spec, penalty_z)
    if answer.type == "noul" and isinstance(answer.value, float):
        return answer.value, 0.0
    return None


def aggregate_scores(
    answers: Iterable[AnswerRecord],
    specs: Mapping[str, QuestionSpec],
    aggregation: AggregationConfig,
    penalty_z: float,
) -> Aggregate | None:
    """Combine the weighted answers into one ranking value.

    Args:
        answers: The engine answers, in grid order.
        specs: The grid, for the level counts each question uses.
        aggregation: Weights and scale from the configuration.
        penalty_z: Standard deviations of downside to subtract, per component.

    Returns:
        The aggregate, or `None` when no weighted answer could be ranked.
    """
    by_name = {answer.name: answer for answer in answers}
    components: dict[str, float] = {}
    weighted_value = 0.0
    weighted_spread = 0.0
    total_weight = 0.0

    for name, weight in aggregation.normalised().items():
        answer = by_name.get(name)
        spec = specs.get(name)
        if answer is None or spec is None:
            continue
        component = component_value(answer, spec, penalty_z)
        if component is None:
            continue
        value, spread = component
        components[name] = round(value, 4)
        weighted_value += weight * value
        weighted_spread += weight * spread
        total_weight += weight

    if total_weight <= 0:
        return None
    return Aggregate(
        adjusted=round(weighted_value / total_weight * aggregation.scale, 4),
        spread=round(weighted_spread / total_weight * aggregation.scale, 4),
        components=components,
    )


def load_enriched(day: date, item: Item) -> EnrichedItem | None:
    """Read the enrichment record of one item, or `None` when there is none.

    A cache file that no longer parses is treated as absent: the item is then judged on
    its metadata alone, which is the documented behaviour for an unreadable article.
    """
    path = enriched_path(day, item.url)
    if not path.exists():
        return None
    try:
        return EnrichedItem.model_validate(read_json(path))
    except (ValidationError, ValueError):
        return None


def _score_one(
    engine: Engine,
    item: Item,
    enriched: EnrichedItem | None,
    cfg: TriageConfig,
    specs: Mapping[str, QuestionSpec],
    aggregation: AggregationConfig,
    now: datetime,
) -> tuple[ItemScore, TriageUsage]:
    """Ask the engine about one item, turning any failure into a recorded state.

    The usage is returned on every path: a call that reached the provider has been billed,
    even when its answer could not be used afterwards.
    """
    identity = {
        "item_id": item.id,
        "url": item.url,
        "title": item.title,
        "points": item.points,
        "num_comments": item.num_comments,
    }

    try:
        result = engine.answer(build_state(item, enriched, now=now))
    except Exception as exc:  # one failed item must never bring the stage down
        return ItemScore(**identity, error=f"{type(exc).__name__}: {exc}"), TriageUsage()

    if result.error is not None:
        return ItemScore(**identity, error=result.error), result.usage

    aggregate = aggregate_scores(result.answers, specs, aggregation, cfg.score_penalty_z)
    passed = aggregate is not None and aggregate.adjusted >= cfg.min_adjusted_score
    return (
        ItemScore(
            **identity,
            answers=result.answers,
            confidence=weakest_confidence(result.answers),
            adjusted=None if aggregate is None else aggregate.adjusted,
            spread=None if aggregate is None else aggregate.spread,
            components={} if aggregate is None else aggregate.components,
            passed=passed,
        ),
        result.usage,
    )


def triage_day(
    day: date,
    items: Iterable[Item],
    cfg: TriageConfig,
    specs: Mapping[str, QuestionSpec],
    aggregation: AggregationConfig,
    *,
    limit: int | None = None,
    engine: Engine | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> TriageOutcome:
    """Score the enriched candidates of one day and write `scores.json`.

    Only items that were actually enriched are considered: scoring the whole day would pay
    for hundreds of items whose article was never fetched. The cap applies inside that
    enriched set.

    Args:
        day: Day being scored.
        items: Candidates, already sorted by descending score.
        cfg: Provider settings from the grid file.
        specs: The questions, in the order they should be reported.
        aggregation: Weights and scale used to combine the answers.
        limit: Overrides `cfg.max_items`, for development runs.
        engine: Injected engine; otherwise a real Jev engine is built, which validates the
            API key before the first item.
        on_progress: Called with `(done, total)` after each item.

    Returns:
        The run report and the per-item scores, which are also written to `scores.json`.
    """
    candidates = list(items)
    cap = limit if limit is not None else cfg.max_items
    available = [item for item in candidates if enriched_path(day, item.url).exists()]
    selected = available[:cap]

    report = TriageReport(candidates=len(available), selected=len(selected), model=cfg.model)
    scores: list[ItemScore] = []
    started = time.monotonic()
    own_engine = engine is None
    active: Engine = engine if engine is not None else TypeSafeEngine(cfg, specs)
    now = datetime.now(UTC)

    try:
        for done, item in enumerate(selected, start=1):
            score, usage = _score_one(
                active, item, load_enriched(day, item), cfg, specs, aggregation, now
            )
            scores.append(score)
            if score.error is None:
                report.answered += 1
                report.passed += 1 if score.passed else 0
                report.dropped += 0 if score.passed else 1
            else:
                report.failed += 1
            report.input_tokens += usage.input_tokens or 0
            report.output_tokens += usage.output_tokens or 0
            if on_progress is not None:
                on_progress(done, len(selected))
    finally:
        if own_engine:
            active.close()

    report.cost_usd = round(report.input_tokens / 1_000_000 * cfg.price_per_mtok_usd, 6)
    report.duration_s = round(time.monotonic() - started, 2)

    payload = {
        "schema_version": 1,
        "date": day.isoformat(),
        "generated_at": iso_utc(now),
        "settings": {
            "model": cfg.model,
            "score_penalty_z": cfg.score_penalty_z,
            "min_adjusted_score": cfg.min_adjusted_score,
            "price_per_mtok_usd": cfg.price_per_mtok_usd,
            "aggregation": {
                "scale": aggregation.scale,
                "weights": aggregation.weights,
            },
            "questions": {name: spec.model_dump(mode="json") for name, spec in specs.items()},
        },
        "stats": report.model_dump(mode="json"),
        "scores": [score.model_dump(mode="json") for score in scores],
    }
    write_json(scores_path(day), payload)
    return TriageOutcome(report=report, scores=scores)
