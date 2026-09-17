"""Tests for the triage stage and the TypeSafe adapter, without any network call."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typesafe_sdk import Choice, Noul, Score, SystemOneResponse, Usage

from veille.clients.typesafe import build_questions, normalise, usage_from
from veille.config import AggregationConfig, QuestionSpec, TriageConfig
from veille.models import (
    AnswerRecord,
    Comment,
    EngineAnswer,
    EnrichedItem,
    Item,
    TriageUsage,
)
from veille.triage import (
    aggregate_scores,
    build_state,
    component_value,
    level_spread,
    load_enriched,
    triage_day,
    weakest_confidence,
)

DAY = date(2026, 9, 16)
NOW = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
SPECS = {
    "interest": QuestionSpec(
        type="score", instructions="Intérêt", criteria=["a", "b", "c", "d", "e"]
    )
}
# One question, one weight, so the aggregate is that question on the configured scale.
AGGREGATION = AggregationConfig(scale=4.0, weights={"interest": 1.0})


def make_item(item_id: str = "1", *, points: int = 100) -> Item:
    return Item(
        id=f"hn:{item_id}",
        source="hn",
        title=f"title {item_id}",
        url=f"https://example.com/{item_id}",
        discussion_url=f"https://news.ycombinator.com/item?id={item_id}",
        author="author",
        points=points,
        num_comments=42,
        published_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
        kind="story",
    )


def make_enriched(item: Item, *, status: str = "ok", comments: int = 1) -> EnrichedItem:
    return EnrichedItem(
        item_id=item.id,
        url=item.url,
        fingerprint="fp",
        status=status,
        fetched_at=NOW,
        title=item.title,
        text="Article body." if status == "ok" else "",
        text_tokens=3,
        comments=[
            Comment(id=f"hn:{n}", author="someone", text="A remark.", published_at=NOW)
            for n in range(comments)
        ],
        error=None if status == "ok" else "ValueError: no main text found",
    )


class FakeEngine:
    """Deterministic stand-in for Jev, answering one score question."""

    model = "fake-engine"

    def __init__(
        self,
        *,
        score: float = 3.0,
        confidence: float = 0.9,
        fail: bool = False,
        probabilities: dict[str, float] | None = None,
    ) -> None:
        self.score = score
        self.confidence = confidence
        self.fail = fail
        # A certain distribution by default, so the ranking penalty stays out of the way
        # unless a test asks for uncertainty.
        self.probabilities = probabilities or {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0}
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def answer(self, state: dict[str, Any]) -> EngineAnswer:
        self.calls.append(state)
        if self.fail:
            raise RuntimeError("engine down")
        answers = [
            AnswerRecord(
                name="interest",
                type="score",
                value=self.score,
                confidence=self.confidence,
                probabilities=self.probabilities,
            )
        ]
        return EngineAnswer(answers=answers, usage=TriageUsage(input_tokens=1500, output_tokens=0))

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cached_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the enriched cache and scores.json into a temporary directory."""
    cache_dir = tmp_path / "enriched"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "veille.triage.enriched_path", lambda day, url: cache_dir / f"{url.rsplit('/', 1)[-1]}.json"
    )
    monkeypatch.setattr("veille.triage.scores_path", lambda day: tmp_path / "scores.json")
    return cache_dir


def write_enriched(cache_dir: Path, item: Item, enriched: EnrichedItem) -> None:
    from veille.store import write_json

    write_json(cache_dir / f"{item.url.rsplit('/', 1)[-1]}.json", enriched.model_dump(mode="json"))


# --- state building -------------------------------------------------------------------


def test_state_carries_metadata_article_and_thread() -> None:
    item = make_item()
    state = build_state(item, make_enriched(item), now=NOW)

    assert state["title"] == "title 1"
    assert state["source_score"] == 100
    assert state["source_comments"] == 42
    assert state["age_hours"] == 22.0
    assert state["article"]["status"] == "ok"
    assert state["article"]["text"] == "Article body."
    assert state["thread"] == [{"author": "someone", "text": "A remark."}]


def test_state_marks_an_unavailable_article_explicitly() -> None:
    item = make_item()
    state = build_state(item, make_enriched(item, status="unavailable"), now=NOW)

    assert state["article"]["status"] == "unavailable"
    assert "ValueError" in state["article"]["reason"]


def test_state_handles_a_missing_enrichment() -> None:
    state = build_state(make_item(), None, now=NOW)

    assert state["article"] == {"status": "unavailable", "reason": "not enriched"}
    assert "thread" not in state


def test_state_has_no_thread_key_without_comments() -> None:
    item = make_item()
    state = build_state(item, make_enriched(item, comments=0), now=NOW)

    assert "thread" not in state


# --- confidence, kept as a diagnostic -------------------------------------------------


def test_weakest_confidence_is_the_lowest() -> None:
    answers = [
        AnswerRecord(name="a", type="score", value=1.0, confidence=0.9),
        AnswerRecord(name="b", type="choice", value="x", confidence=0.4),
    ]
    assert weakest_confidence(answers) == 0.4


def test_weakest_confidence_ignores_noul_answers() -> None:
    assert weakest_confidence([AnswerRecord(name="a", type="noul", value=0.8)]) is None


def test_weakest_confidence_of_nothing_is_none() -> None:
    assert weakest_confidence([]) is None


# --- ranking rule ---------------------------------------------------------------------


def test_spread_is_zero_when_the_engine_is_certain() -> None:
    assert level_spread({"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0}) == 0.0


def test_spread_of_adjacent_levels_stays_small() -> None:
    """Hesitating between "useful" and "important" is precision, not ignorance."""
    adjacent = level_spread({"0": 0.0, "1": 0.01, "2": 0.37, "3": 0.43, "4": 0.19})
    assert adjacent is not None
    assert adjacent == pytest.approx(0.75, abs=0.01)


def test_spread_of_opposite_levels_is_large() -> None:
    assert level_spread({"0": 0.5, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.5}) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "probabilities",
    [{}, {"a": 1.0}, {"0": 0.0, "1": 0.0}],
)
def test_an_unusable_distribution_has_no_spread(probabilities: dict[str, float]) -> None:
    assert level_spread(probabilities) is None


SPEC = QuestionSpec(type="score", criteria=["0", "1", "2", "3", "4"])


def test_component_value_normalises_a_score_to_the_unit_interval() -> None:
    answer = AnswerRecord(
        name="interest",
        type="score",
        value=2.0,
        confidence=0.9,
        probabilities={"2": 1.0},
    )
    assert component_value(answer, SPEC, 1.0) == pytest.approx((0.5, 0.0))
    assert component_value(answer, SPEC, 0.0) == pytest.approx((0.5, 0.0))


def test_component_value_subtracts_z_spreads_of_its_own_distribution() -> None:
    answer = AnswerRecord(
        name="interest",
        type="score",
        value=2.0,
        confidence=0.5,
        probabilities={"0": 0.5, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.5},
    )
    value, spread = component_value(answer, SPEC, 1.0)

    assert spread == pytest.approx(0.5)  # 2.0 on the level scale, over four levels
    assert value == pytest.approx(0.0)  # 2.0 - 2.0, then normalised
    assert component_value(answer, SPEC, 0.0)[0] == pytest.approx(0.5)


def test_component_value_of_a_noul_is_its_probability() -> None:
    answer = AnswerRecord(name="primary_source", type="noul", value=0.8)
    spec = QuestionSpec(type="noul", instructions="primary?")
    assert component_value(answer, spec, 1.0) == (0.8, 0.0)


def test_component_value_of_a_choice_does_not_rank() -> None:
    answer = AnswerRecord(name="category", type="choice", value="tooling")
    spec = QuestionSpec(type="choice", criteria={"tooling": "a tool"})
    assert component_value(answer, spec, 1.0) is None


def test_a_high_score_with_neighbouring_uncertainty_beats_a_certain_middle_score() -> None:
    """The measurement that replaced the confidence filter.

    On the first real run the best-scored item of the day had a confidence of 0.51 only
    because its mass sat between two high, neighbouring levels, while an item firmly at
    level 2 came back at 0.80. Ranking on the lower bound puts them back in order.
    """
    nvidia = AnswerRecord(
        name="interest",
        type="score",
        value=2.80,
        confidence=0.51,
        probabilities={"0": 0.00, "1": 0.01, "2": 0.37, "3": 0.43, "4": 0.19},
    )
    mistral = AnswerRecord(
        name="interest",
        type="score",
        value=2.08,
        confidence=0.80,
        probabilities={"0": 0.01, "1": 0.06, "2": 0.80, "3": 0.10, "4": 0.03},
    )

    high_and_uncertain = component_value(nvidia, SPEC, 1.0)[0]
    middle_and_certain = component_value(mistral, SPEC, 1.0)[0]

    assert high_and_uncertain > middle_and_certain


# --- weighted aggregation -------------------------------------------------------------


def test_the_aggregate_normalises_the_scales_before_weighting() -> None:
    """The scoping notes' formula added a position on 0-4 to a probability on 0-1.

    Read literally it gave the primary-source signal a real weight of 5 % where it looked
    like 20 %. With every component normalised first, an essential, shallow, primary item
    lands at 4 x (0.5 x 1.0 + 0.3 x 0.0 + 0.2 x 1.0) = 2.8.
    """
    specs = {
        "interest": QuestionSpec(type="score", criteria=["a"] * 5),
        "density": QuestionSpec(type="score", criteria=["a"] * 5),
        "primary_source": QuestionSpec(type="noul"),
    }
    answers = [
        AnswerRecord(name="interest", type="score", value=4.0, probabilities={"4": 1.0}),
        AnswerRecord(name="density", type="score", value=0.0, probabilities={"0": 1.0}),
        AnswerRecord(name="primary_source", type="noul", value=1.0),
    ]
    aggregation = AggregationConfig(
        scale=4.0, weights={"interest": 0.5, "density": 0.3, "primary_source": 0.2}
    )

    result = aggregate_scores(answers, specs, aggregation, penalty_z=0.0)

    assert result is not None
    assert result.adjusted == pytest.approx(2.8)
    assert result.components == {"interest": 1.0, "density": 0.0, "primary_source": 1.0}


def test_weights_are_relative_and_get_normalised() -> None:
    specs = {"interest": QuestionSpec(type="score", criteria=["a"] * 5)}
    answers = [AnswerRecord(name="interest", type="score", value=4.0, probabilities={"4": 1.0})]

    for weights in ({"interest": 1.0}, {"interest": 5.0}, {"interest": 0.2}):
        result = aggregate_scores(
            answers, specs, AggregationConfig(scale=4.0, weights=weights), penalty_z=0.0
        )
        assert result is not None
        assert result.adjusted == pytest.approx(4.0)


def test_an_unweighted_question_does_not_change_the_aggregate() -> None:
    specs = {
        "interest": QuestionSpec(type="score", criteria=["a"] * 5),
        "category": QuestionSpec(type="choice", criteria={"tooling": "a tool"}),
    }
    aggregation = AggregationConfig(scale=4.0, weights={"interest": 1.0})
    without = [AnswerRecord(name="interest", type="score", value=3.0, probabilities={"3": 1.0})]
    with_category = without + [AnswerRecord(name="category", type="choice", value="tooling")]

    assert (
        aggregate_scores(without, specs, aggregation, penalty_z=0.0).adjusted
        == aggregate_scores(with_category, specs, aggregation, penalty_z=0.0).adjusted
    )


def test_a_missing_weighted_answer_is_skipped_rather_than_counted_as_zero() -> None:
    specs = {
        "interest": QuestionSpec(type="score", criteria=["a"] * 5),
        "density": QuestionSpec(type="score", criteria=["a"] * 5),
    }
    aggregation = AggregationConfig(scale=4.0, weights={"interest": 0.5, "density": 0.5})
    answers = [AnswerRecord(name="interest", type="score", value=3.0, probabilities={"3": 1.0})]

    result = aggregate_scores(answers, specs, aggregation, penalty_z=0.0)

    assert result is not None
    assert result.adjusted == pytest.approx(3.0)
    assert result.components == {"interest": 0.75}


def test_an_aggregate_without_any_rankable_answer_is_none() -> None:
    specs = {"category": QuestionSpec(type="choice", criteria={"x": "y"})}
    aggregation = AggregationConfig(scale=4.0, weights={"category": 1.0})
    answers = [AnswerRecord(name="category", type="choice", value="x")]

    assert aggregate_scores(answers, specs, aggregation, penalty_z=1.0) is None


# --- cache reading --------------------------------------------------------------------


def test_load_enriched_returns_none_without_a_cache_file(cached_day: Path) -> None:
    assert load_enriched(DAY, make_item()) is None


def test_load_enriched_returns_none_on_a_corrupt_file(cached_day: Path) -> None:
    item = make_item()
    (cached_day / "1.json").write_text("{ not json", encoding="utf-8")
    assert load_enriched(DAY, item) is None


# --- the run --------------------------------------------------------------------------


def test_only_enriched_items_are_scored(cached_day: Path) -> None:
    enriched_item = make_item("1")
    write_enriched(cached_day, enriched_item, make_enriched(enriched_item))
    plain_item = make_item("2", points=90)

    engine = FakeEngine()
    outcome = triage_day(
        DAY, [enriched_item, plain_item], TriageConfig(), SPECS, AGGREGATION, engine=engine
    )

    assert (outcome.report.candidates, outcome.report.selected) == (1, 1)
    assert len(engine.calls) == 1


def test_scores_json_records_the_grid_next_to_the_scores(cached_day: Path, tmp_path: Path) -> None:
    import json

    item = make_item()
    write_enriched(cached_day, item, make_enriched(item))

    triage_day(
        DAY, [item], TriageConfig(min_adjusted_score=1.0), SPECS, AGGREGATION, engine=FakeEngine()
    )

    payload = json.loads((tmp_path / "scores.json").read_text(encoding="utf-8"))
    assert payload["settings"]["min_adjusted_score"] == 1.0
    assert payload["settings"]["score_penalty_z"] == 1.0
    assert payload["settings"]["questions"]["interest"]["type"] == "score"
    assert payload["settings"]["questions"]["interest"]["criteria"] == ["a", "b", "c", "d", "e"]
    assert payload["settings"]["aggregation"]["weights"] == {"interest": 1.0}
    assert payload["scores"][0]["answers"][0]["name"] == "interest"
    assert payload["scores"][0]["answers"][0]["value"] == 3.0


def test_an_item_above_the_adjusted_threshold_passes(cached_day: Path) -> None:
    item = make_item()
    write_enriched(cached_day, item, make_enriched(item))

    outcome = triage_day(
        DAY,
        [item],
        TriageConfig(min_adjusted_score=1.0),
        SPECS,
        AGGREGATION,
        engine=FakeEngine(score=3.0),
    )

    assert outcome.scores[0].passed is True
    assert outcome.scores[0].adjusted == 3.0
    assert outcome.scores[0].spread == 0.0
    assert (outcome.report.passed, outcome.report.dropped) == (1, 0)


def test_the_downside_penalty_drops_an_uncertain_item(cached_day: Path) -> None:
    """One standard deviation of downside, not the raw score, is what the threshold sees."""
    item = make_item()
    write_enriched(cached_day, item, make_enriched(item))
    # Mass split between "no interest" and "essential": the mean is 2.0, the spread 2.0.
    split = {"0": 0.5, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.5}

    outcome = triage_day(
        DAY,
        [item],
        TriageConfig(min_adjusted_score=1.0, score_penalty_z=1.0),
        SPECS,
        AGGREGATION,
        engine=FakeEngine(score=2.0, probabilities=split),
    )

    assert outcome.scores[0].spread == pytest.approx(2.0)
    assert outcome.scores[0].adjusted == pytest.approx(0.0)
    assert outcome.scores[0].passed is False
    assert (outcome.report.passed, outcome.report.dropped) == (0, 1)


def test_a_certain_middle_score_passes_where_a_split_one_fails(cached_day: Path) -> None:
    item = make_item()
    write_enriched(cached_day, item, make_enriched(item))
    certain = {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0}

    outcome = triage_day(
        DAY,
        [item],
        TriageConfig(min_adjusted_score=1.0),
        SPECS,
        AGGREGATION,
        engine=FakeEngine(score=2.0, probabilities=certain),
    )

    assert outcome.scores[0].adjusted == pytest.approx(2.0)
    assert outcome.scores[0].passed is True


def test_a_failing_item_is_recorded_and_the_run_continues(cached_day: Path) -> None:
    first, second = make_item("1"), make_item("2", points=90)
    write_enriched(cached_day, first, make_enriched(first))
    write_enriched(cached_day, second, make_enriched(second))

    outcome = triage_day(
        DAY, [first, second], TriageConfig(), SPECS, AGGREGATION, engine=FakeEngine(fail=True)
    )

    assert outcome.report.failed == 2
    assert outcome.report.answered == 0
    assert all("RuntimeError" in (score.error or "") for score in outcome.scores)


def test_usage_is_summed_and_the_cost_computed(cached_day: Path) -> None:
    items = [make_item("1"), make_item("2", points=90)]
    for item in items:
        write_enriched(cached_day, item, make_enriched(item))

    outcome = triage_day(
        DAY, items, TriageConfig(price_per_mtok_usd=0.042), SPECS, AGGREGATION, engine=FakeEngine()
    )

    assert outcome.report.input_tokens == 3000
    assert outcome.report.cost_usd == pytest.approx(3000 / 1_000_000 * 0.042)


def test_limit_overrides_the_cap(cached_day: Path) -> None:
    items = [make_item(str(n), points=100 - n) for n in range(4)]
    for item in items:
        write_enriched(cached_day, item, make_enriched(item))

    outcome = triage_day(
        DAY,
        items,
        TriageConfig(max_items=200),
        SPECS,
        AGGREGATION,
        limit=2,
        engine=FakeEngine(),
    )

    assert (outcome.report.candidates, outcome.report.selected) == (4, 2)


def test_an_injected_engine_is_left_open_for_its_owner(cached_day: Path) -> None:
    item = make_item()
    write_enriched(cached_day, item, make_enriched(item))
    engine = FakeEngine()

    triage_day(DAY, [item], TriageConfig(), SPECS, AGGREGATION, engine=engine)

    assert engine.closed is False


def test_progress_is_reported_once_per_item(cached_day: Path) -> None:
    items = [make_item(str(n), points=100 - n) for n in range(3)]
    for item in items:
        write_enriched(cached_day, item, make_enriched(item))
    seen: list[tuple[int, int]] = []

    triage_day(
        DAY,
        items,
        TriageConfig(),
        SPECS,
        AGGREGATION,
        engine=FakeEngine(),
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1, 3), (2, 3), (3, 3)]


# --- the SDK adapter ------------------------------------------------------------------


def test_configured_questions_become_sdk_objects() -> None:
    specs = {
        "interest": QuestionSpec(type="score", instructions="i", criteria=["a", "b"]),
        "category": QuestionSpec(type="choice", instructions="c", criteria={"x": "d"}),
        "primary": QuestionSpec(type="noul", instructions="n"),
    }
    questions = build_questions(specs)

    assert isinstance(questions["interest"], Score)
    assert isinstance(questions["category"], Choice)
    assert isinstance(questions["primary"], Noul)
    assert list(questions) == ["interest", "category", "primary"]


def test_answers_are_normalised_in_grid_order_and_missing_ones_are_skipped() -> None:
    response = SystemOneResponse(
        model="jev-latest",
        usage=Usage(input_tokens=1234, output_tokens=0),
        answers={
            "interest": pytest.importorskip("typesafe_sdk").ScoreAnswer(
                score=2.35,
                confidence=0.77,
                legend={"0": "aucun", "1": "marginal"},
                probabilities={"0": 0.1, "1": 0.9},
            )
        },
    )
    specs = {
        "interest": QuestionSpec(type="score", criteria=["a", "b"]),
        "absent": QuestionSpec(type="noul"),
    }

    records, error = normalise(specs, response)

    assert error is None
    assert [record.name for record in records] == ["interest"]
    assert records[0].type == "score"
    assert records[0].value == 2.35
    assert records[0].confidence == 0.77
    assert records[0].probabilities == {"0": 0.1, "1": 0.9}
    assert records[0].legend == {"0": "aucun", "1": "marginal"}


def test_integer_legend_keys_are_coerced_to_strings() -> None:
    """Jev keys the score legend by level, and the level index arrives as a number."""
    answer = SimpleNamespace(
        score=2.0,
        confidence=0.8,
        legend={0: "aucun", 1: "marginal"},
        probabilities={0: 0.2, 1: 0.8},
    )
    response = SystemOneResponse(
        model="jev-latest", usage=Usage(input_tokens=10), answers={"interest": answer}
    )

    records, error = normalise({"interest": QuestionSpec(type="score", criteria=["a"])}, response)

    assert error is None
    assert records[0].legend == {"0": "aucun", "1": "marginal"}
    assert records[0].probabilities == {"0": 0.2, "1": 0.8}


def test_an_unreadable_answer_is_reported_without_raising() -> None:
    broken = SimpleNamespace(score="not a number", confidence=0.8, legend={}, probabilities={})
    response = SystemOneResponse(
        model="jev-latest", usage=Usage(input_tokens=10), answers={"interest": broken}
    )

    records, error = normalise({"interest": QuestionSpec(type="score", criteria=["a"])}, response)

    assert records == []
    assert error is not None
    assert error.startswith("interest: ")


def test_usage_is_read_from_the_response() -> None:
    response = SystemOneResponse(
        model="jev-latest",
        usage=Usage(input_tokens=1234, output_tokens=7),
        answers={},
    )
    usage = usage_from(response)

    assert usage.input_tokens == 1234
    assert usage.output_tokens == 7


def test_missing_token_counts_stay_none() -> None:
    response = SystemOneResponse(model="jev-latest", usage=Usage(input_tokens=5), answers={})
    usage = usage_from(response)

    assert usage.input_tokens == 5
    assert usage.output_tokens is None
