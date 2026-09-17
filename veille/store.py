"""Arborescence des données, empreintes d'URL et écriture de fichiers.

Les étapes du pipeline communiquent par fichiers : ce module centralise où ils
vivent et comment ils sont écrits, pour qu'aucune étape n'invente son propre
chemin. Aucune base de données en V1.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date as Date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DIGEST_DIR = ROOT / "digest"
SEEN_PATH = ROOT / "seen.jsonl"


def data_dir(day: Date) -> Path:
    return DATA_DIR / day.isoformat()


def enriched_dir(day: Date) -> Path:
    return data_dir(day) / "enriched"


def items_path(day: Date) -> Path:
    return data_dir(day) / "items.json"


def scores_path(day: Date) -> Path:
    return data_dir(day) / "scores.json"


def digest_path(day: Date) -> Path:
    return DIGEST_DIR / f"{day.isoformat()}.md"


def url_fingerprint(url: str) -> str:
    """Empreinte stable d'une URL, utilisée comme nom de fichier de cache.

    16 caractères hexadécimaux (64 bits) : suffisant pour un cache local, et
    lisible dans une arborescence qu'on inspecte à la main.
    """
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def write_json(path: Path, payload: Any) -> None:
    """Écrit un JSON de façon atomique : fichier temporaire puis renommage.

    Un run interrompu ne laisse donc jamais un `items.json` tronqué derrière lui.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
