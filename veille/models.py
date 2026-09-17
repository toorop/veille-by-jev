"""Modèles partagés par les étapes du pipeline.

Ces structures sont le contrat entre les commandes : elles sont écrites dans les
fichiers de `data/` et relues par l'étape suivante. Toute évolution de forme doit
donc être réfléchie comme un changement d'interface, pas comme un détail interne.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Window(BaseModel):
    """Fenêtre temporelle couverte par une collecte.

    `start` et `end` sont en UTC et toujours exprimés comme une demi-ouverture
    `[start, end)` : une story publiée exactement à `end` appartient au
    lendemain. C'est ce qui garantit qu'une même date donne toujours le même lot.
    """

    model_config = ConfigDict(frozen=True)

    day: Date = Field(description="Journée civile couverte, dans le fuseau `timezone`.")
    timezone: str = Field(description="Fuseau utilisé pour découper la journée civile.")
    start: datetime = Field(description="Début de fenêtre, en UTC.")
    end: datetime = Field(description="Fin de fenêtre exclue, en UTC.")

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


class Item(BaseModel):
    """Un item collecté, avant enrichissement.

    Champs volontairement pauvres : ce que la source sait dire sans lire
    l'article. Le texte et les commentaires arrivent à l'étape 2.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Identifiant stable, préfixé par la source : `hn:<objectID>`.")
    source: str = Field(description="Nom court de la source : `hn`.")
    title: str
    url: str = Field(description="URL de l'article, ou du fil de discussion si l'item n'en a pas.")
    discussion_url: str = Field(description="URL du fil de discussion sur la source.")
    author: str | None = None
    points: int = Field(ge=0, description="Score de la source (points HN).")
    num_comments: int = Field(ge=0)
    published_at: datetime = Field(description="Horodatage de publication, en UTC.")
    kind: str = Field(
        default="story",
        description="Nature de l'item côté source : `story`, `show_hn`, `ask_hn`…",
    )


class SourceReport(BaseModel):
    """Ce qu'une source a fait pendant la collecte, y compris en cas d'échec.

    Un échec de source est journalisé ici et ne fait pas tomber l'étape.
    """

    source: str
    label: str = ""
    status: Literal["ok", "error"]
    requests: int = 0
    fetched: int = Field(default=0, description="Items bruts renvoyés par la source.")
    kept: int = Field(default=0, description="Items retenus après filtres et troncature.")
    duration_s: float = 0.0
    error: str | None = None


class CollectOutcome(BaseModel):
    """Résultat d'une source : ses items plus son rapport d'exécution."""

    items: list[Item] = Field(default_factory=list)
    report: SourceReport
