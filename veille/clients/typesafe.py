"""Adapter around the official TypeSafe SDK, which serves Jev.

Everything provider-specific lives here: how a configured question becomes an SDK question
object, how the three answer primitives are normalised, and the call itself. The rest of the
pipeline only ever sees `AnswerRecord` and `TriageUsage`, so the engine can be replaced by a
local ranking model without touching the triage stage.

Notes taken from the SDK source (version 0.6.0), not from its marketing:

- one call carries the state and every question, and the state is ingested once;
- `Score` answers come back as a position on the scale that can land between two levels,
  with a confidence and a distribution;
- `Choice` answers carry a confidence and a distribution;
- `Noul` answers carry only a probability, with no separate confidence field.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Any

from typesafe_sdk import (
    Choice,
    Noul,
    RetryPolicy,
    Score,
    SystemOneResponse,
    TypeSafeClient,
)

from veille.config import QuestionSpec, TriageConfig
from veille.models import AnswerRecord, EngineAnswer, TriageUsage


def build_questions(specs: Mapping[str, QuestionSpec]) -> dict[str, Any]:
    """Turn the configured grid into the SDK question objects.

    Args:
        specs: Questions read from `config/questions.toml`, in file order.

    Returns:
        A mapping of question name to SDK question, in the same order.
    """
    questions: dict[str, Any] = {}
    for name, spec in specs.items():
        if spec.type == "score":
            questions[name] = Score(
                instructions=spec.instructions, criteria=list(spec.criteria or [])
            )
        elif spec.type == "choice":
            questions[name] = Choice(
                instructions=spec.instructions, criteria=dict(spec.criteria or {})
            )
        else:
            questions[name] = Noul(instructions=spec.instructions)
    return questions


def _optional_int(value: object) -> int | None:
    """Return `value` as an int, or `None` when the provider left it unset."""
    return int(value) if isinstance(value, int) else None


def usage_from(response: SystemOneResponse) -> TriageUsage:
    """Read the token accounting of one call.

    Only `input_tokens` and `output_tokens` are exposed by the SDK; the protocol's
    `billing_units` field never reaches the public response object.
    """
    usage = response.usage
    return TriageUsage(
        input_tokens=_optional_int(usage.input_tokens),
        output_tokens=_optional_int(usage.output_tokens),
    )


def _record(name: str, kind: str, answer: Any) -> AnswerRecord:
    """Normalise one SDK answer object into an `AnswerRecord`.

    The score legend comes back keyed by level, and those keys can be integers: JSON only
    has string keys, but the SDK decodes the level index as a number, so they are coerced
    here rather than assumed.
    """
    if kind == "score":
        return AnswerRecord(
            name=name,
            type="score",
            value=float(answer.score),
            confidence=float(answer.confidence),
            probabilities={str(key): float(value) for key, value in answer.probabilities.items()},
            legend={str(key): value for key, value in dict(answer.legend).items()},
        )
    if kind == "choice":
        return AnswerRecord(
            name=name,
            type="choice",
            value=str(answer.choice),
            confidence=float(answer.confidence),
            probabilities={str(key): float(value) for key, value in answer.probabilities.items()},
        )
    return AnswerRecord(name=name, type="noul", value=float(answer.noul))


def normalise(
    specs: Mapping[str, QuestionSpec], response: SystemOneResponse
) -> tuple[list[AnswerRecord], str | None]:
    """Normalise every answer of a response, in the order of the grid.

    An answer the provider did not return is skipped rather than invented, and an answer
    that cannot be read is reported instead of raising: the call has already been billed,
    so the run must keep its accounting and carry on.

    Args:
        specs: The questions, in the order they should be reported.
        response: The provider response.

    Returns:
        The records that could be read, and the first failure encountered, if any.
    """
    records: list[AnswerRecord] = []
    error: str | None = None
    for name, spec in specs.items():
        answer = response.answers.get(name)
        if answer is None:
            continue
        try:
            records.append(_record(name, spec.type, answer))
        except Exception as exc:
            error = error or f"{name}: {type(exc).__name__}: {exc}"
    return records, error


class TypeSafeEngine:
    """Answers the configured questions about one state, through Jev.

    Building the engine validates the API key: a missing or empty key raises here, before
    any item is sent, rather than once per item.
    """

    def __init__(
        self,
        cfg: TriageConfig,
        specs: Mapping[str, QuestionSpec],
        client: TypeSafeClient | None = None,
    ) -> None:
        """Build the engine, validating the API key straight away.

        Args:
            cfg: Provider settings from the grid file.
            specs: Questions to ask, in the order they should be reported.
            client: Pre-built client, injected by tests; otherwise the SDK builds one.
        """
        self.model = cfg.model
        self._cfg = cfg
        self._specs = dict(specs)
        self._questions = build_questions(specs)
        self._client = (
            client
            if client is not None
            else TypeSafeClient(
                model=cfg.model,
                timeout=cfg.timeout_s,
                retry=RetryPolicy(max_retries=cfg.max_retries),
            )
        )

    def answer(self, state: Mapping[str, Any]) -> EngineAnswer:
        """Ask every configured question about one state.

        A transport or credential failure propagates; a response that cannot be fully read
        comes back as a result carrying its error, never as an exception, so the caller
        keeps the cost of the call it just paid for.

        Args:
            state: The item state, as built by `veille.triage.build_state`.

        Returns:
            The normalised answers, the token accounting of the call, and any local
            reading failure.
        """
        response = self._client.system_one(state=dict(state), questions=self._questions)
        answers, error = normalise(self._specs, response)
        return EngineAnswer(answers=answers, usage=usage_from(response), error=error)

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> TypeSafeEngine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
