"""Tests for the writing stage: selection, answer parsing, assembly and idempotence.

The writing model is a stand-in, so no test calls a provider.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from veille.clients.openrouter import ChatReply
from veille.config import (
    AggregationConfig,
    QuestionsConfig,
    QuestionSpec,
    TriageConfig,
    WriteConfig,
)
from veille.models import AnswerRecord, EnrichedItem, ItemScore
from veille.write import (
    build_state,
    parse_reply,
    render_digest,
    select_kept,
    write_day,
)

DAY = date(2026, 9, 16)


def make_score(item_id: str, adjusted: float | None, *, category: str = "tooling") -> ItemScore:
    return ItemScore(
        item_id=f"hn:{item_id}",
        url=f"https://example.com/{item_id}",
        title=f"Title {item_id}",
        points=100,
        num_comments=10,
        adjusted=adjusted,
        spread=0.4,
        components={"interest": 0.6},
        answers=[
            AnswerRecord(name="interest", type="score", value=2.5, confidence=0.8),
            AnswerRecord(name="category", type="choice", value=category, confidence=0.9),
        ],
    )


class FakeChat:
    """Stand-in writer, returning a well-formed JSON answer by default."""

    def __init__(self, content: str | None = None) -> None:
        self.content = content
        self.calls: list[str] = []
        self.model = "fake-writer"

    def complete(self, system_prompt: str, user_prompt: str) -> ChatReply:
        self.calls.append(user_prompt)
        if self.content is None:
            ids = [item["id"] for item in json.loads(user_prompt)["items"]]
            self.content = json.dumps(
                {
                    "items": [
                        {
                            "id": item_id,
                            "synthese": "Une synthèse en français.",
                            "pourquoi": "Parce que cela compte.",
                        }
                        for item_id in ids
                    ]
                }
            )
        return ChatReply(
            content=self.content,
            model=self.model,
            input_tokens=1000,
            output_tokens=200,
            cost_usd=0.0004,
            duration_s=1.5,
        )


@pytest.fixture
def grid() -> QuestionsConfig:
    return QuestionsConfig(
        triage=TriageConfig(min_adjusted_score=2.0),
        aggregation=AggregationConfig(scale=4.0, weights={"interest": 1.0}),
        questions={
            "interest": QuestionSpec(type="score", criteria=["a", "b", "c", "d", "e"]),
            "category": QuestionSpec(type="choice", criteria={"tooling": "a tool"}),
        },
    )


@pytest.fixture
def prompt_file(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.md"
    path.write_text("Tu écris un digest.", encoding="utf-8")
    return path


@pytest.fixture
def write_cfg(prompt_file: Path) -> WriteConfig:
    return WriteConfig(
        model="fake/model",
        digest_size=2,
        prompt_path=str(prompt_file),
        labels={"tooling": "outillage", "research": "recherche"},
    )


# --- selection ------------------------------------------------------------------------


def test_selection_applies_the_floor_then_the_size() -> None:
    scores = [make_score(str(n), value) for n, value in enumerate([0.5, 3.0, 2.0, 2.5, 1.9])]

    kept, dropped = select_kept(scores, floor=2.0, size=2)

    assert [score.item_id for score in kept] == ["hn:1", "hn:3"]
    assert [score.item_id for score in dropped] == ["hn:2", "hn:4", "hn:0"]
    assert len(kept) + len(dropped) == len(scores)


def test_selection_of_a_quiet_night_keeps_what_it_can() -> None:
    scores = [make_score("1", 2.1), make_score("2", 0.5)]

    kept, dropped = select_kept(scores, floor=2.0, size=8)

    assert [score.item_id for score in kept] == ["hn:1"]
    assert [score.item_id for score in dropped] == ["hn:2"]


def test_an_item_without_an_aggregate_is_set_aside() -> None:
    kept, dropped = select_kept([make_score("1", None)], floor=0.0, size=8)

    assert kept == []
    assert len(dropped) == 1


# --- parsing --------------------------------------------------------------------------


def test_a_clean_answer_is_read() -> None:
    prose, error = parse_reply('{"items": [{"id": "a", "synthese": "S", "pourquoi": "P"}]}')

    assert error is None
    assert prose == {"a": {"synthese": "S", "pourquoi": "P"}}


def test_text_around_the_json_is_tolerated() -> None:
    raw = 'Voici le résultat :\n```json\n{"items": [{"id": "a", "synthese": "S"}]}\n```'

    prose, error = parse_reply(raw)

    assert error is None
    assert prose["a"]["synthese"] == "S"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pas de json ici", "no JSON object"),
        ('{"items": "pas une liste"}', "no `items` list"),
        ('{"items": []}', "empty"),
        ('{"items": [{"id": "a"', "not valid JSON"),
    ],
)
def test_a_malformed_answer_is_reported(raw: str, expected: str) -> None:
    prose, error = parse_reply(raw)

    assert prose == {}
    assert error is not None
    assert expected in error


def test_entries_without_an_id_are_skipped() -> None:
    prose, error = parse_reply('{"items": [{"synthese": "S"}, {"id": "b", "synthese": "T"}]}')

    assert error is None
    assert list(prose) == ["b"]


# --- assembly -------------------------------------------------------------------------


def test_the_digest_is_built_from_the_data_not_from_the_model() -> None:
    kept = [make_score("1", 2.5, category="tooling")]
    dropped = [make_score("2", 1.2, category="research")]
    reply = ChatReply(
        content="",
        model="fake/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        duration_s=1.0,
    )

    digest = render_digest(
        DAY,
        kept,
        dropped,
        prose={"hn:1": {"synthese": "Résumé.", "pourquoi": "Raison."}},
        reply=reply,
        labels={"tooling": "outillage", "research": "recherche"},
        floor=2.0,
        considered=42,
    )

    assert digest.startswith("# Veille du 16 septembre 2026")
    assert "**1 items retenus** sur 42 candidats triés" in digest
    assert "Seuil d'admission : 2,0" in digest
    assert "- **Lien** : <https://example.com/1>" in digest
    assert "- **Catégorie** : outillage" in digest
    assert "agrégat 2,50" in digest
    assert "Résumé." in digest
    assert "**Pourquoi celui-là.** Raison." in digest
    assert "## Items écartés" in digest
    assert "| 1,20 | recherche | [Title 2](<https://example.com/2>) |" in digest
    assert "0,001000 USD, mesuré par le fournisseur" in digest


def test_a_missing_summary_is_flagged_in_place() -> None:
    digest = render_digest(
        DAY,
        [make_score("1", 2.5)],
        [],
        prose={},
        reply=ChatReply(
            content="", model="m", input_tokens=1, output_tokens=1, cost_usd=None, duration_s=0.1
        ),
        labels={},
        floor=2.0,
        considered=1,
    )

    assert "_Synthèse manquante" in digest
    assert "non communiqué par le fournisseur" in digest
    # Without a label mapping the raw key is used rather than nothing.
    assert "- **Catégorie** : tooling" in digest


# --- the run --------------------------------------------------------------------------


@pytest.fixture
def scores_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect scores.json and the enriched cache into a temporary directory."""
    path = tmp_path / "scores.json"
    monkeypatch.setattr("veille.write.scores_path", lambda day: path)
    monkeypatch.setattr(
        "veille.write.enriched_path", lambda day, url: tmp_path / f"{url.rsplit('/', 1)[-1]}.json"
    )
    return path


def write_scores(path: Path, scores: list[ItemScore]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "settings": {},
                "scores": [score.model_dump(mode="json") for score in scores],
            }
        ),
        encoding="utf-8",
    )


def test_the_digest_is_written_to_the_requested_path(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5), make_score("2", 1.0)])
    target = tmp_path / "out" / "digest.md"

    outcome = write_day(DAY, write_cfg, grid, output=target, chat=FakeChat())

    assert target.exists()
    assert outcome.report.kept == 1
    assert outcome.report.dropped == 1
    assert outcome.report.input_tokens == 1000
    assert outcome.report.cost_usd == 0.0004
    assert outcome.report.warning is None
    assert target.read_text(encoding="utf-8") == outcome.markdown


def test_the_state_carries_the_article_and_the_questions(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5)])
    enriched = EnrichedItem(
        item_id="hn:1",
        url="https://example.com/1",
        fingerprint="fp",
        status="ok",
        fetched_at=datetime.now(UTC),
        title="Title 1",
        text="Le corps de l'article.",
        text_tokens=5,
    )
    (tmp_path / "1.json").write_text(enriched.model_dump_json(), encoding="utf-8")
    chat = FakeChat()

    write_day(DAY, write_cfg, grid, output=tmp_path / "d.md", chat=chat)

    assert len(chat.calls) == 1
    state = json.loads(chat.calls[0])
    assert state["date"] == "2026-09-16"
    item = state["items"][0]
    assert item["id"] == "hn:1"
    assert item["article"]["text"] == "Le corps de l'article."
    assert item["answers"] == {"interest": 2.5, "category": "tooling"}


def test_an_existing_digest_is_not_written_again(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5)])
    target = tmp_path / "d.md"
    target.write_text("déjà écrit", encoding="utf-8")
    chat = FakeChat()

    outcome = write_day(DAY, write_cfg, grid, output=target, chat=chat)

    assert chat.calls == []
    assert target.read_text(encoding="utf-8") == "déjà écrit"
    assert outcome.markdown == "déjà écrit"


def test_force_writes_again(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5)])
    target = tmp_path / "d.md"
    target.write_text("ancien", encoding="utf-8")
    chat = FakeChat()

    write_day(DAY, write_cfg, grid, output=target, chat=chat, force=True)

    assert len(chat.calls) == 1
    assert "Veille du" in target.read_text(encoding="utf-8")


def test_a_malformed_answer_leaves_a_reserve_in_the_digest(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5)])
    broken = FakeChat(content="je ne sais pas répondre en JSON")
    target = tmp_path / "d.md"

    outcome = write_day(DAY, write_cfg, grid, output=target, chat=broken)

    assert outcome.report.warning is not None
    text = target.read_text(encoding="utf-8")
    assert "**Réserve sur ce digest**" in text
    assert "_Synthèse manquante" in text
    # Le digest annonce où trouver la réponse brute : elle doit donc vraiment y être.
    raw = scores_file.with_name("write-raw-answer.txt")
    assert raw.read_text(encoding="utf-8") == broken.content
    assert "write-raw-answer.txt" in text


def test_missing_scores_is_an_explicit_error(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match="triage"):
        write_day(DAY, write_cfg, grid, output=tmp_path / "d.md", chat=FakeChat())


def test_a_missing_prompt_file_is_an_explicit_error(
    scores_file: Path, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5)])
    cfg = WriteConfig(model="m", digest_size=1, prompt_path=str(tmp_path / "absent.md"))

    with pytest.raises(FileNotFoundError, match="System prompt"):
        write_day(DAY, cfg, grid, output=tmp_path / "d.md", chat=FakeChat())


def test_the_state_lists_only_what_the_digest_keeps() -> None:
    kept = [make_score("1", 2.5)]
    state = json.loads(build_state(DAY, kept, {}))

    assert [item["id"] for item in state["items"]] == ["hn:1"]
    assert state["items"][0]["article"] == {"status": "unavailable", "reason": "not enriched"}


def test_an_unreadable_article_is_declared_as_such_in_the_state() -> None:
    item = make_score("1", 2.5)
    enriched = EnrichedItem(
        item_id="hn:1",
        url=item.url,
        fingerprint="fp",
        status="unavailable",
        fetched_at=datetime.now(UTC),
        title="Title 1",
        error="ValueError: no main text found",
    )

    state = json.loads(build_state(DAY, [item], {item.url: enriched}))

    assert state["items"][0]["article"]["status"] == "unavailable"
    assert "no main text found" in state["items"][0]["article"]["reason"]


def test_the_prompt_file_is_read_and_sent_as_the_system_message(
    scores_file: Path, write_cfg: WriteConfig, grid: QuestionsConfig, tmp_path: Path
) -> None:
    write_scores(scores_file, [make_score("1", 2.5)])
    seen: dict[str, Any] = {}

    class RecordingChat(FakeChat):
        def complete(self, system_prompt: str, user_prompt: str) -> ChatReply:
            seen["system"] = system_prompt
            return super().complete(system_prompt, user_prompt)

    write_day(DAY, write_cfg, grid, output=tmp_path / "d.md", chat=RecordingChat())

    assert seen["system"] == "Tu écris un digest."


def test_a_dropped_title_is_a_link_and_cannot_break_the_table() -> None:
    """A vertical bar or a bracket in a title would split the row or close the link."""
    hostile = ItemScore(
        item_id="hn:3",
        url="https://example.com/a_(b)",
        title="Un titre | avec [des] crochets",
        adjusted=1.1,
        answers=[AnswerRecord(name="interest", type="score", value=1.0)],
    )

    digest = render_digest(
        DAY,
        [],
        [hostile],
        prose={},
        reply=ChatReply(
            content="", model="m", input_tokens=1, output_tokens=1, cost_usd=None, duration_s=0.1
        ),
        labels={},
        floor=2.0,
        considered=1,
    )

    row = next(line for line in digest.splitlines() if line.startswith("| 1,10 |"))
    # Seuls les pipes non échappés délimitent la ligne : le titre échappé n'en ajoute aucun.
    assert len(re.findall(r"(?<!\\)\|", row)) == 4
    assert "Un titre \\| avec \\[des\\] crochets" in row
    assert "](<https://example.com/a_(b)>)" in row
