"""Collecte Hacker News via l'API Algolia.

Choix de conception :

- **Fenêtre civile, pas « 24 h en arrière ».** La fenêtre est calculée par
  `config.day_window` : `--date 2026-09-16` couvre le 16 septembre de 00:00 à
  24:00 heure locale, quelle que soit l'heure du run. C'est ce qui rend la
  collecte rejouable à l'identique.
- **Tri par points, pas `front_page`.** `front_page` ne contient que ce qui est
  sur la page d'accueil au moment du run : la composition du lot dépendrait de
  l'heure d'exécution. Ici on interroge tout ce qui a été publié dans la
  fenêtre, puis on garde les meilleurs scores.
- **Filtre de score seulement, pas de troncature finale.** La source écarte les
  items sous `min_points` et rend tout le reste, trié par score ; la troncature
  à `target_items` est faite une seule fois, au niveau du pipeline, pour rester
  correcte le jour où plusieurs sources coexistent.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from veille.config import CollectConfig, SourceSettings
from veille.models import CollectOutcome, Item, SourceReport, Window

# Note de couverture : l'API Algolia plafonne à `MAX_HITS_PER_QUERY` résultats
# paginables par requête (voir `veille.config`), donc au-delà, les items les plus
# anciens de la fenêtre ne sont pas vus. Sur une journée HN ordinaire (≈1 100
# stories le 2026-09-16) la couverture reste quasi complète, et de toute façon ce
# sont les meilleurs scores qu'on garde.

# Ordre de priorité : un Show HN porte à la fois les tags `story` et `show_hn`,
# c'est le tag le plus spécifique qui décrit l'item.
_STORY_KINDS = ("show_hn", "ask_hn", "job", "story")


def _kind_from_tags(tags: list[str]) -> str:
    present = set(tags)
    for kind in _STORY_KINDS:
        if kind in present:
            return kind
    return "story"


def _item_from_hit(hit: dict) -> Item | None:
    """Convertit un hit Algolia en `Item`, ou `None` si le hit est inexploitable."""
    object_id = hit.get("objectID")
    created_at_i = hit.get("created_at_i")
    if not object_id or created_at_i is None:
        return None

    discussion_url = f"https://news.ycombinator.com/item?id={object_id}"
    title = (hit.get("title") or "").strip() or "(sans titre)"
    return Item(
        id=f"hn:{object_id}",
        source="hn",
        title=title,
        url=(hit.get("url") or "").strip() or discussion_url,
        discussion_url=discussion_url,
        author=hit.get("author"),
        points=int(hit.get("points") or 0),
        num_comments=int(hit.get("num_comments") or 0),
        published_at=datetime.fromtimestamp(int(created_at_i), tz=timezone.utc),
        kind=_kind_from_tags(list(hit.get("_tags") or [])),
    )


def _fetch_page(client: httpx.Client, cfg: SourceSettings, window: Window, page: int) -> dict:
    params = {
        "tags": cfg.tags,
        "numericFilters": (
            f"created_at_i>={int(window.start.timestamp())},"
            f"created_at_i<{int(window.end.timestamp())}"
        ),
        "hitsPerPage": cfg.hits_per_page,
        "page": page,
    }
    response = client.get(cfg.endpoint, params=params)
    response.raise_for_status()
    return response.json()


def collect(
    window: Window,
    cfg: SourceSettings,
    collect_cfg: CollectConfig,
    source_name: str = "hn",
) -> CollectOutcome:
    """Collecte les meilleurs items HN de la fenêtre. Ne lève jamais : l'échec est rapporté."""
    started = time.monotonic()
    label = cfg.label or source_name
    report = SourceReport(source=source_name, label=label, status="error")
    hits: list[dict] = []

    try:
        with httpx.Client(
            timeout=cfg.timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "veille-by-jev/0.1 (collecte personnelle Hacker News)"},
        ) as client:
            for page in range(cfg.max_pages):
                payload = _fetch_page(client, cfg, window, page)
                report.requests += 1
                page_hits = payload.get("hits") or []
                hits.extend(page_hits)
                # On s'arrête sur page incomplète, ou sur la dernière page annoncée
                # par l'API (sinon un lot de 1000 hits déclenche une requête vide).
                if len(page_hits) < cfg.hits_per_page or page + 1 >= int(payload.get("nbPages") or 1):
                    break
    except (httpx.HTTPError, ValueError) as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        report.duration_s = round(time.monotonic() - started, 2)
        return CollectOutcome(items=[], report=report)

    items = [item for item in (_item_from_hit(hit) for hit in hits) if item is not None]
    report.fetched = len(items)

    # Un même article peut être posté deux fois : on garde la meilleure entrée.
    best_by_url: dict[str, Item] = {}
    for item in items:
        current = best_by_url.get(item.url)
        if current is None or (item.points, item.num_comments) > (current.points, current.num_comments):
            best_by_url[item.url] = item

    retained = [item for item in best_by_url.values() if item.points >= collect_cfg.min_points]
    retained.sort(key=lambda item: (item.points, item.num_comments, item.published_at), reverse=True)

    report.status = "ok"
    report.kept = len(retained)
    report.duration_s = round(time.monotonic() - started, 2)
    return CollectOutcome(items=retained, report=report)
