"""Configuration du pipeline : lecture de `config/*.toml` et calcul des fenêtres.

Règle du projet : la grille et les paramètres vivent dans les fichiers TOML, et
un changement de configuration ne doit jamais demander de toucher au code.
"""

from __future__ import annotations

import tomllib
from datetime import date as Date
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from veille.models import Window
from veille.store import CONFIG_DIR

# Plafond de l'API Algolia : au-delà de 1000 résultats, la pagination s'arrête.
# Une seule requête à ce plafond couvre donc une journée HN entière.
MAX_HITS_PER_QUERY = 1000


class CollectConfig(BaseModel):
    """Paramètres communs à toutes les sources."""

    timezone: str = Field(default="Europe/Paris", description="Fuseau des journées civiles.")
    target_items: int = Field(default=200, ge=1, description="Items retenus par nuit.")
    min_points: int = Field(default=1, ge=0, description="Score source minimal pour être retenu.")


class SourceSettings(BaseModel):
    """Une source déclarée dans `config/sources.toml`."""

    enabled: bool = True
    type: Literal["hn"] = "hn"
    label: str = ""
    endpoint: str
    tags: str = "story"
    hits_per_page: int = Field(default=MAX_HITS_PER_QUERY, ge=1, le=MAX_HITS_PER_QUERY)
    max_pages: int = Field(default=1, ge=1, le=10)
    timeout_s: float = Field(default=30.0, gt=0)


class SourcesConfig(BaseModel):
    collect: CollectConfig = Field(default_factory=CollectConfig)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)

    def enabled_sources(self) -> dict[str, SourceSettings]:
        return {name: cfg for name, cfg in self.sources.items() if cfg.enabled}


def load_sources_config(path: Path | None = None) -> SourcesConfig:
    """Charge `config/sources.toml`. Lève une erreur explicite si le fichier manque."""
    config_path = path or (CONFIG_DIR / "sources.toml")
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration absente : {config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return SourcesConfig.model_validate(raw)


def day_window(day: Date, tz_name: str) -> Window:
    """Fenêtre `[jour 00:00, jour+1 00:00)` dans `tz_name`, exprimée en UTC.

    Fenêtre civile plutôt que « 24 h en arrière depuis maintenant » : le même
    `--date` produit toujours le même lot, quel que soit le moment du run.
    """
    try:
        tz = ZoneInfo(tz_name)
    except KeyError as exc:  # pragma: no cover - dépend du fuseau système
        raise ValueError(f"Fuseau inconnu : {tz_name!r}") from exc

    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return Window(day=day, timezone=tz_name, start=start, end=end)
