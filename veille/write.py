"""Stage 4: writing the digest.

The writer is the only stage that produces text, and it is given a deliberately narrow job:
turn each kept item's state into a French summary — a few sentences up to about fifteen, however
many the explanation needs — and a "why this one" line.
Everything structural — the dated title, the links, the sources, the scores, the set-aside
table and the cost line — is generated here from the data, so the model can neither misquote a
figure nor forget a link.

The same reasoning explains the JSON contract: the model answers with prose keyed by item id,
and the markdown is assembled deterministically. A malformed answer is recorded as an error and
the raw reply is kept, rather than silently producing a broken digest.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from veille.clients.openrouter import API_KEY_ENV as WRITE_LLM_API_KEY_ENV
from veille.clients.openrouter import ChatReply, OpenRouterChat
from veille.config import QuestionsConfig, WriteConfig
from veille.models import EnrichedItem, ItemScore, WriteOutcome, WriteReport
from veille.store import digest_path, enriched_path, iso_utc, read_json, scores_path

MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


class Chat(Protocol):
    """What the writing stage needs from a language model."""

    def complete(self, system_prompt: str, user_prompt: str) -> ChatReply:
        """Answer one prompt."""
        ...


def select_kept(
    scores: list[ItemScore], floor: float, size: int
) -> tuple[list[ItemScore], list[ItemScore]]:
    """Split the night's scores into what the digest keeps and what it sets aside.

    Args:
        scores: Every scored item, as written by triage.
        floor: Admission floor on the aggregate.
        size: Number of items the digest writes up.

    Returns:
        The kept items, best first, and everything else, best first.
    """
    ranked = sorted(
        scores,
        key=lambda score: score.adjusted if score.adjusted is not None else -1.0,
        reverse=True,
    )
    admitted = [score for score in ranked if score.adjusted is not None and score.adjusted >= floor]
    kept = admitted[:size]
    kept_ids = {score.item_id for score in kept}
    dropped = [score for score in ranked if score.item_id not in kept_ids]
    return kept, dropped


def build_state(
    day: date,
    kept: list[ItemScore],
    enriched_by_url: Mapping[str, EnrichedItem | None],
) -> str:
    """Build the user prompt: the kept items, as JSON.

    Args:
        day: Day being written up.
        kept: Items the digest keeps.
        enriched_by_url: Enrichment records, keyed by URL.

    Returns:
        The JSON state, ready to send.
    """
    payload: dict[str, Any] = {"date": day.isoformat(), "items": []}
    for score in kept:
        enriched = enriched_by_url.get(score.url)
        answered = {answer.name: answer.value for answer in score.answers}
        item: dict[str, Any] = {
            "id": score.item_id,
            "title": score.title,
            "url": score.url,
            "source_score": score.points,
            "source_comments": score.num_comments,
            "aggregate": score.adjusted,
            "answers": answered,
        }
        if enriched is None:
            item["article"] = {"status": "unavailable", "reason": "not enriched"}
        else:
            item["article"] = {
                "status": enriched.status,
                "truncated": enriched.text_truncated,
                "text": enriched.text,
                "reason": enriched.error,
            }
            if enriched.comments:
                item["thread"] = [
                    {"author": comment.author, "text": comment.text}
                    for comment in enriched.comments
                ]
        payload["items"].append(item)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_reply(content: str) -> tuple[dict[str, dict[str, str]], str | None]:
    """Read the model's JSON answer, tolerating text around it.

    Args:
        content: The raw answer.

    Returns:
        The prose keyed by item id, and why reading failed when it did.
    """
    start = content.find("{")
    if start < 0:
        return {}, "the answer holds no JSON object"

    try:
        body, _end = json.JSONDecoder().raw_decode(content[start:])
    except ValueError as exc:
        return {}, f"the answer is not valid JSON: {exc}"

    raw_items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(raw_items, list):
        return {}, "the answer has no `items` list"

    prose: dict[str, dict[str, str]] = {}
    for entry in raw_items:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        prose[str(entry["id"])] = {
            "synthese": str(entry.get("synthese") or "").strip(),
            "pourquoi": str(entry.get("pourquoi") or "").strip(),
        }
    return prose, None if prose else "the `items` list is empty"


def _cell(text: str) -> str:
    """Escape what would break a Markdown table cell.

    A title holding a vertical bar would split the row, and an unescaped bracket would
    close the link early. The URL goes between angle brackets for the same reason: a
    address holding parentheses is common enough on the web.
    """
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _french_number(value: float, digits: int = 2) -> str:
    """Format a number the French way, with a comma as the decimal separator."""
    return f"{value:.{digits}f}".replace(".", ",")


def _label(value: str, labels: Mapping[str, str]) -> str:
    """Return the French label of a choice answer, falling back to the raw value."""
    return labels.get(value, value)


def render_digest(
    day: date,
    kept: list[ItemScore],
    dropped: list[ItemScore],
    prose: Mapping[str, Mapping[str, str]],
    reply: ChatReply,
    labels: Mapping[str, str],
    floor: float,
    considered: int,
) -> str:
    """Assemble the digest: the model's prose inside a structure built from the data.

    Args:
        day: Day being written up.
        kept: Items the digest keeps, best first.
        dropped: Items set aside, best first.
        prose: The model's prose, keyed by item id.
        reply: What the call cost.
        labels: French labels for the category values.
        floor: Admission floor that was applied.
        considered: Number of items triaged that night.

    Returns:
        The digest, as Markdown.
    """
    lines = [
        f"# Veille du {day.day} {MONTHS_FR[day.month - 1]} {day.year}",
        "",
        f"**{len(kept)} items retenus** sur {considered} candidats triés. "
        f"Seuil d'admission : {_french_number(floor, 1)}.",
        "",
    ]

    for score in kept:
        text = prose.get(score.item_id, {})
        answered = {answer.name: answer.value for answer in score.answers}
        details = " · ".join(
            f"{name} {_french_number(float(value), 1)}"
            for name, value in answered.items()
            if isinstance(value, (int, float)) and name != "category"
        )
        category = _label(str(answered.get("category", "")), labels)

        lines += [
            f"## {score.title}",
            "",
            f"- **Source** : Hacker News — {score.points} points, "
            f"{score.num_comments} commentaires",
            f"- **Lien** : <{score.url}>",
        ]
        if category:
            lines.append(f"- **Catégorie** : {category}")
        if details:
            aggregate = score.adjusted or 0.0
            lines.append(f"- **Scores** : {details} · agrégat {_french_number(aggregate)}")
        lines.append("")

        summary = (
            text.get("synthese")
            or "_Synthèse manquante : le modèle n'a pas répondu pour cet item._"
        )
        lines += [summary, ""]
        why = text.get("pourquoi")
        if why:
            lines += [f"**Pourquoi celui-là.** {why}", ""]

    lines += [
        "---",
        "",
        "## Items écartés",
        "",
        f"Les {len(dropped)} items triés qui ne sont pas retenus, du meilleur au moins bon, "
        "pour que le tri reste contestable.",
        "",
        "| Agrégat | Catégorie | Titre (lien vers l'article) |",
        "| --- | --- | --- |",
    ]
    for score in dropped:
        answered = {answer.name: answer.value for answer in score.answers}
        category = _label(str(answered.get("category", "")), labels) or "—"
        aggregate = "—" if score.adjusted is None else _french_number(score.adjusted)
        lines.append(f"| {aggregate} | {category} | [{_cell(score.title)}](<{score.url}>) |")

    cost = (
        f"{_french_number(reply.cost_usd, 6)} USD, mesuré par le fournisseur"
        if reply.cost_usd is not None
        else "non communiqué par le fournisseur"
    )
    lines += [
        "",
        "## Coût du run",
        "",
        f"- Modèle : `{reply.model}`",
        f"- Tokens d'entrée : {reply.input_tokens} · sortie : {reply.output_tokens}",
        f"- Coût : {cost}",
        f"- Écrit le {iso_utc(datetime.now(UTC))}",
        "",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class PreparedPrompt:
    """Everything a writing run needs, before any model is called.

    Attributes:
        user_prompt: The exact state that would be sent, as JSON.
        kept: Items the digest keeps, best first.
        dropped: Items set aside, best first.
        floor: Admission floor that was applied.
        considered: Number of items triaged that night.
    """

    user_prompt: str
    kept: list[ItemScore]
    dropped: list[ItemScore]
    floor: float
    considered: int


def prepare_prompt(day: date, cfg: WriteConfig, grid: QuestionsConfig) -> PreparedPrompt:
    """Read the night's scores, choose what the digest keeps, and build the user prompt.

    Separated from `write_day` so the exact state can be dumped and inspected — or fed to a
    model comparison outside this pipeline — without calling anything.

    Args:
        day: Day to write up.
        cfg: Writing settings, for the digest size.
        grid: The grid, for the admission floor.

    Returns:
        The prompt and the selection behind it.

    Raises:
        FileNotFoundError: If `scores.json` is missing.
    """
    source = scores_path(day)
    if not source.exists():
        raise FileNotFoundError(f"{source} not found: run `vbj triage --date {day}` first.")

    scores = [ItemScore.model_validate(raw) for raw in read_json(source)["scores"]]
    floor = grid.triage.min_adjusted_score
    kept, dropped = select_kept(scores, floor, cfg.digest_size)

    enriched_by_url: dict[str, EnrichedItem | None] = {}
    for score in kept:
        path = enriched_path(day, score.url)
        enriched_by_url[score.url] = (
            EnrichedItem.model_validate(read_json(path)) if path.exists() else None
        )

    return PreparedPrompt(
        user_prompt=build_state(day, kept, enriched_by_url),
        kept=kept,
        dropped=dropped,
        floor=floor,
        considered=len(scores),
    )


def write_day(
    day: date,
    cfg: WriteConfig,
    grid: QuestionsConfig,
    *,
    model: str | None = None,
    output: Path | None = None,
    chat: Chat | None = None,
    force: bool = False,
) -> WriteOutcome:
    """Write the digest of one day.

    Args:
        day: Day to write up.
        cfg: Writing settings.
        grid: The grid, for the admission floor and the question names.
        model: Overrides the configured model, for comparing writers.
        output: Overrides the output path, so a comparison does not overwrite the digest.
        chat: Injected writer; otherwise a real OpenRouter client is built, which validates
            the key before anything is assembled.
        force: Write again even when the digest already exists.

    Returns:
        The report and the Markdown digest.

    Raises:
        ChatError: If the provider refuses the call.
        FileNotFoundError: If `scores.json` is missing.
    """
    prepared = prepare_prompt(day, cfg, grid)
    kept, dropped = prepared.kept, prepared.dropped
    floor, considered = prepared.floor, prepared.considered

    destination = output or digest_path(day)
    if destination.exists() and not force:
        return WriteOutcome(
            report=WriteReport(
                considered=considered,
                kept=len(kept),
                dropped=len(dropped),
                floor=floor,
                model=model or cfg.model,
                digest_path=str(destination),
            ),
            markdown=destination.read_text(encoding="utf-8"),
        )

    prompt_file = cfg.prompt_file()
    if not prompt_file.exists():
        raise FileNotFoundError(f"System prompt not found: {prompt_file}")

    effective = cfg.model_copy(update={"model": model}) if model else cfg
    active: Chat = (
        chat
        if chat is not None
        else OpenRouterChat(effective, api_key=os.environ.get(WRITE_LLM_API_KEY_ENV, ""))
    )
    started = time.monotonic()
    reply = active.complete(prompt_file.read_text(encoding="utf-8"), prepared.user_prompt)
    prose, error = parse_reply(reply.content)

    markdown = render_digest(
        day,
        kept,
        dropped,
        prose,
        reply,
        labels=cfg.labels,
        floor=floor,
        considered=considered,
    )
    if error is not None:
        # The raw answer is kept next to the day's data: a truncated reply is the one case
        # where the digest cannot be trusted, and guessing from it would waste the call.
        raw_path = scores_path(day).with_name("write-raw-answer.txt")
        raw_path.write_text(reply.content, encoding="utf-8")
        markdown += (
            "\n> **Réserve sur ce digest** : la réponse du modèle n'a pas pu être lue "
            f"intégralement ({error}). Les synthèses manquantes sont signalées ci-dessus, "
            f"et la réponse brute est conservée dans `{raw_path.name}`.\n"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")

    return WriteOutcome(
        report=WriteReport(
            considered=considered,
            kept=len(kept),
            dropped=len(dropped),
            floor=floor,
            model=reply.model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_usd=reply.cost_usd,
            duration_s=round(time.monotonic() - started, 2),
            digest_path=str(destination),
            warning=error,
        ),
        markdown=markdown,
    )
